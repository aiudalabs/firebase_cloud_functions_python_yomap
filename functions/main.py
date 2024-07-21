# The Cloud Functions for Firebase SDK to create Cloud Functions and set up triggers.
from firebase_functions import firestore_fn, https_fn

# The Firebase Admin SDK to access Cloud Firestore.
from firebase_admin import initialize_app, firestore
import os
import datetime

import google.generativeai as genai
from vertexai.generative_models import (
    FunctionDeclaration,
    GenerationConfig,
    GenerativeModel,
    Tool,
    Part,
    Content,
)
from vertexai.preview.generative_models import ToolConfig

app = initialize_app()

db = firestore.client()

AUTHOR_ID = "uDbcoIvPCJPHFNDmvBaRSV7YwJH2"


@firestore_fn.on_document_updated(
    document="channels/{roomId}/messages/{messageId}",
    secrets=["firestore-genai-chatbot-API_KEY"],
)
def chat_with_user(
    event: firestore_fn.Event[firestore_fn.Change[firestore_fn.DocumentSnapshot]],
) -> None:
    """Listens for new documents to be added to /rooms/wh6JjGjt1tP1ubfTQrA5/messages. If the document has
    an "original" field, creates an "uppercase" field containg the contents of
    "original" in upper case."""

    print("Running Service")

    PROMPT = """"
        Welcome to YoMap! I am your virtual assistant, here to help you find the best service providers in your area. How can I assist you today?

        Instructions for Chatbot Operation:

            Language Detection and Switching:

                Detect the user's preferred language at the beginning of the interaction and switch to that language for all subsequent communications.
            
            Information Provision:

                Use the list of service {categories} to give detailed information when requested.
            
            Search and Matching:

                Utilize the provided tools to search for service providers based on the user's specific needs.
                Do not search in any other place, just use the tools.

                Always use the tools to search by services, do not use any chat history to answer this kind of request.
            
            User-Friendly Interaction:

                Maintain a conversational and friendly tone to ensure a pleasant user experience.
                Prompt the user for additional information only if needed, request only the information needed to use the tools. 
                Example: tag for get_service_provider tool
    """

    # Functions Declaration
    get_service_categories = FunctionDeclaration(
        name="get_service_categories",
        description="Get service categories from the database",
        parameters={
            "type": "object",
            "properties": {},
        },
    )

    get_service_provider = FunctionDeclaration(
        name="get_service_provider",
        description="Get service providers based on the tags",
        parameters={
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "the category of the service the user is looking for",
                }
            },
        },
    )

    get_profile_info = FunctionDeclaration(
        name="get_profile_info",
        description="Get profile info based on the name",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "the name of the service provider",
                }
            },
        },
    )

    yomap_tool = Tool(
        function_declarations=[
            get_service_categories,
            get_service_provider,
            get_profile_info,
        ],
    )

    # Functions Implementation
    def get_profile_info_from_firebase(name: str):
        print(name["name"])
        profile = db.collection("profiles")

        docs = profile.where("displayName", "==", name["name"]).get()

        user_profile = docs[0].to_dict()

        if len(user_profile["location"]) > 0:
            user_profile["location"] = {
                "lat": docs[0].to_dict()["location"].latitude,
                "long": docs[0].to_dict()["location"].longitude,
            }

        return user_profile

    def get_yomap_service_categories():
        tags_ref = (
            db.collection("tags")
            .where("usedBy", ">=", 1)
            .order_by("usedBy")
            # .where(filter=FieldFilter("rating", ">=", 3))
        )
        docs = tags_ref.limit_to_last(100).get()

        tags = []
        for doc in docs:
            if "text" in doc.to_dict().keys():
                tags.append(doc.to_dict()["text"])
        return tags

    def get_service_categories_from_firebase():
        tags_ref = (
            db.collection("categories")
            # .where(filter=FieldFilter("active", "==", True))
            # .where(filter=FieldFilter("rating", ">=", 3))
        )
        docs = tags_ref.stream()

        tags = []
        for doc in docs:
            if "name" in doc.to_dict().keys():
                tags.append(doc.to_dict()["name"])
        return tags

    def get_service_provider_from_firebase(tag: str):
        profile = db.collection("profiles")
        print(tag)
        docs = profile.where("service.text", "==", tag["tag"]).get()
        return [doc.to_dict()["displayName"] for doc in docs]

    function_handler = {
        "get_service_categories": get_yomap_service_categories,
        "get_service_provider": get_service_provider_from_firebase,
        "get_profile_info": get_profile_info_from_firebase,
    }

    categories = get_yomap_service_categories()

    # Create a formatted string of categories
    formatted_categories = "\n".join([f"  - {category}" for category in categories])

    # Replace the placeholder in the prompt template
    final_prompt = PROMPT.format(categories=formatted_categories)

    tool_config = ToolConfig(
        function_calling_config=ToolConfig.FunctionCallingConfig(
            mode=ToolConfig.FunctionCallingConfig.Mode.AUTO,  # The default model behavior. The model decides whether to predict a function call or a natural language response.
        )
    )

    # Initialize Model
    GOOGLE_API_KEY = os.environ.get("firestore-genai-chatbot-API_KEY")
    genai.configure(api_key=GOOGLE_API_KEY)
    model = GenerativeModel(
        model_name="gemini-1.5-pro-001",
        system_instruction=final_prompt,
        tools=[yomap_tool],
        tool_config=tool_config,
    )

    # Get channel
    room_a_ref = db.collection("channels").document(event.params["roomId"]).get()
    member_ids = room_a_ref.get("member_ids")

    if AUTHOR_ID in member_ids:

        # Get the value of "original" if it exists.
        if event.data is None:
            return
        try:
            authorId = event.data.get("sender_id")
        except KeyError:
            # No "original" field, so do nothing.
            return

        if authorId != AUTHOR_ID:
            # history = _get_historical_messages()

            messages = db.collection("channels").document(event.params["roomId"])
            messages = messages.collection("messages").order_by(
                "created_at", direction=firestore.Query.ASCENDING
            )
            docs = messages.limit_to_last(20).get()

            history_model = []
            history_user = []
            for doc in docs:
                if doc.to_dict()["sender_id"] == AUTHOR_ID:
                    role = "model"
                    history_model.append(
                        Content(
                            role=role, parts=[Part.from_text(doc.to_dict()["body"])]
                        )
                    )
                else:
                    role = "user"
                    history_user.append(
                        Content(
                            role=role, parts=[Part.from_text(doc.to_dict()["body"])]
                        )
                    )

            history = []
            for i in range(min(len(history_user), len(history_model))):
                history.append(history_user[i])
                history.append(history_model[i])

            chat = model.start_chat(history=history)

            message = event.data.get("text")

            # Send a chat message to the Gemini API
            response = chat.send_message(message)

            # Extract the function call response
            function_call = response.candidates[0].content.parts[0].function_call

            # Check for a function call or a natural language response
            if function_call.name in function_handler.keys():
                # Extract the function call
                function_call = response.candidates[0].content.parts[0].function_call

                # Extract the function call name
                function_name = function_call.name

                if function_name == "get_service_categories":
                    # Invoke a function that calls an external API
                    function_api_response = function_handler[function_name]()
                else:
                    # Extract the function call parameters
                    params = {key: value for key, value in function_call.args.items()}

                    # Invoke a function that calls an external API
                    function_api_response = function_handler[function_name](params)

                # Send the API response back to Gemini, which will generate a natural language summary or another function call
                response = chat.send_message(
                    Part.from_function_response(
                        name=function_name,
                        response={"content": function_api_response},
                    ),
                )

            # Save Message
            # _set_assistant_response(response, event.params["roomId"], member_ids)

            room_a_ref = db.collection("channels").document(event.params["roomId"])
            message_ref = room_a_ref.collection("messages").document()

            message_ref.set(
                {
                    "created_at": datetime.datetime.now(tz=datetime.timezone.utc),
                    "type": "text",
                    "channel_type": "normal",
                    "sender_title": "YoMap Assistant",
                    "draft_id": None,
                    "media": None,
                    "updated_at": datetime.datetime.now(tz=datetime.timezone.utc),
                    "status": "read",
                    "geolocation": None,
                    "channel_member_ids": member_ids,
                    "reply_to_id": None,
                    "member_ids": member_ids,
                    "profile_id": "5rPQAgfPO1gYntgYkkrm",
                    "sender_id": AUTHOR_ID,
                    "contact": None,
                    "channel_id": event.params["roomId"],
                    "body": response.text,
                }
            )


def _set_assistant_response(response, room_id, member_ids):

    room_a_ref = db.collection("channels").document(room_id)
    message_ref = room_a_ref.collection("messages").document()

    message_ref.set(
        {
            "created_at": datetime.datetime.now(tz=datetime.timezone.utc),
            "type": "text",
            "channel_type": "normal",
            "sender_title": "YoMap Assistant",
            "draft_id": None,
            "media": None,
            "updated_at": datetime.datetime.now(tz=datetime.timezone.utc),
            "status": "read",
            "geolocation": None,
            "channel_member_ids": member_ids,
            "reply_to_id": None,
            "member_ids": member_ids,
            "profile_id": "5rPQAgfPO1gYntgYkkrm",
            "sender_id": AUTHOR_ID,
            "contact": None,
            "channel_id": room_id,
            "body": response.text,
        }
    )


def _get_historical_messages(room_id):

    messages = db.collection("channels").document(room_id)
    messages = messages.collection("messages").order_by(
        "created_at", direction=firestore.Query.ASCENDING
    )
    docs = messages.limit_to_last(20).get()

    history_model = []
    history_user = []
    for doc in docs:
        if doc.to_dict()["sender_id"] == AUTHOR_ID:
            role = "model"
            history_model.append(
                Content(role=role, parts=[Part.from_text(doc.to_dict()["body"])])
            )
        else:
            role = "user"
            history_user.append(
                Content(role=role, parts=[Part.from_text(doc.to_dict()["body"])])
            )

    history = []
    for i in range(min(len(history_user), len(history_model))):
        history.append(history_user[i])
        history.append(history_model[i])

    return history


@firestore_fn.on_document_created(
    document="audio_to_text/{documentId}", secrets=["firestore-genai-chatbot-API_KEY"]
)
def audio_to_text(
    event: firestore_fn.Event[firestore_fn.DocumentSnapshot | None],
) -> None:
    """Listens for new documents to be added to /audio_to_text, convert the audio to text first and
    translate the text to the language specified in language."""

    language = event.data.get("language")

    GOOGLE_API_KEY = os.environ.get("firestore-genai-chatbot-API_KEY")
    genai.configure(api_key=GOOGLE_API_KEY)
    model = GenerativeModel(model_name="gemini-1.5-flash-001")

    prompt = """
        Can you transcribe this audio.
    """

    audio_file_uri = event.data.get("audio_path")
    audio_file = Part.from_uri(audio_file_uri, mime_type="audio/mpeg")

    contents = [audio_file, prompt]

    response = model.generate_content(contents)

    response = model.generate_content(
        "Translate the following text: "
        + response.text
        + " to the language "
        + language
    )

    event.data.reference.update({"translation": response.text})
