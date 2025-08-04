import os
import json
import base64
import asyncio
import websockets
import traceback
import aiohttp
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from twilio.rest import Client
from dotenv import load_dotenv
import logging
import pprint

# Enhanced logging configuration
logging.basicConfig(
    level=logging.INFO,  # Changed to INFO from DEBUG to reduce logging
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configure websockets logging - reduce its verbosity
logging.getLogger('websockets').setLevel(logging.INFO)  # Changed to INFO

# This class filters out websocket text messages to reduce noise
class WebSocketFilter(logging.Filter):
    def filter(self, record):
        if hasattr(record, 'message'):
            # Skip audio data logs
            if 'input_audio_buffer.append' in record.message:
                return False
            if '"audio":' in record.message and len(record.message) > 100:
                return False
        return True

# Add filter to websockets logger
websockets_logger = logging.getLogger('websockets')
websockets_logger.addFilter(WebSocketFilter())

load_dotenv()
# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')  # requires OpenAI Realtime API Access
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
MAIN_API_URL = os.getenv('MAIN_API_URL')
ORG_ID = os.getenv('ORG_ID')

# Add detailed logging to verify environment variables
logger.info("=== Environment Variables Debug ===")
logger.info(f"Current working directory: {os.getcwd()}")
logger.info(f"Environment file location: {os.path.abspath('.env')}")
logger.info(f"TWILIO_ACCOUNT_SID: {TWILIO_ACCOUNT_SID}")
logger.info(f"TWILIO_AUTH_TOKEN length: {len(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else 0}")
logger.info(f"TWILIO_PHONE_NUMBER: {TWILIO_PHONE_NUMBER}")
logger.info(f"MAIN_API_URL: {MAIN_API_URL}")
logger.info(f"ORG_ID: {ORG_ID}")
logger.info("===================================")

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')
if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
    raise ValueError('Missing Twilio credentials. Please set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER in the .env file.')
if not MAIN_API_URL or not ORG_ID:
    raise ValueError('Missing API configuration. Please set MAIN_API_URL and ORG_ID in the .env file.')

# Initialize Twilio client
twilio_client = Client(
    os.getenv('TWILIO_ACCOUNT_SID'),
    os.getenv('TWILIO_AUTH_TOKEN')
)

PORT = int(os.getenv('PORT', 5050))
MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini-realtime-preview')

logger.info(f"Using model: {MODEL}")
logger.info(f"API key (first/last 4 chars): {OPENAI_API_KEY[:4]}...{OPENAI_API_KEY[-4:]}")

# Define tool configuration in the correct format as shown in the API example
TOOLS = [
    # {
    #     "type": "function",
    #     "name": "get_weather",
    #     "description": "Get the current weather in a given location",
    #     "parameters": {
    #         "type": "object",
    #         "properties": {
    #             "location": {
    #                 "type": "string",
    #                 "description": "The city and state, e.g., San Francisco, CA"
    #             }
    #         },
    #         "required": ["location"]
    #     }
    # },
    # {
    #     "type": "function",
    #     "name": "get_course_categories",
    #     "description": "Get a summary of all course categories and their counts. Will be called ALWAYS when someone asks about course offerings or categories.",
    #     "parameters": {
    #         "type": "object",
    #         "properties": {},  # No parameters needed
    #         "required": []
    #     }
    # },
    # {
    #     "type": "function",
    #     "name": "get_courses_by_category",
    #     "description": "Get a list of courses in a specific category. Will be called when someone asks to see what courses are offered in a specific category.",
    #     "parameters": {
    #         "type": "object",
    #         "properties": {
    #             "category_name": {
    #                 "type": "string",
    #                 "description": "The name of the category to get courses for"
    #             }
    #         },
    #         "required": ["category_name"]
    #     }
    # },
    # {
    #     "type": "function",
    #     "name": "get_course_dates",
    #     "description": "Get upcoming dates and times for a specific course",
    #     "parameters": {
    #         "type": "object",
    #         "properties": {
    #             "activity_id": {
    #                 "type": "string",
    #                 "description": "The unique ID of the course to get dates for"
    #             }
    #         },
    #         "required": ["activity_id"]
    #     }
    # },
    # {
    #     "type": "function",
    #     "name": "send_course_signup_link",
    #     "description": "Send a signup link for a specific course event to a phone number",
    #     "parameters": {
    #         "type": "object",
    #         "properties": {
    #             "event_id": {
    #                 "type": "string",
    #                 "description": "The unique ID of the course event to sign up for"
    #             },
    #             "phone_number": {
    #                 "type": "string",
    #                 "description": "The phone number to send the signup link to"
    #             }
    #         },
    #         "required": ["event_id", "phone_number"]
    #     }
    # }
]

SYSTEM_MESSAGE = """
You are Gio, the AI-powered training assistant for Workbench. You’re conducting a voice-based simulation for a caller who is participating in a remote training demo.

Your tone is clear, technical, and supportive — like an expert instructor guiding a student through real-world diagnostics. You do not talk about anything off-topic. You stay focused on the training.

The caller has already completed Session #6 at a Workbench-affiliated facility and is now performing an in-lab follow-up simulation using real tools. You will guide them step-by-step through troubleshooting and testing using multimeters, continuity tests, voltage logic checks, and, optionally, oscilloscopes or clamp meters.

OPENING SCRIPT
"Welcome to this training demo, powered by Workbench.
I can simulate two hands-on training modules for you today.
Would you like to explore the HVAC module or the Electrician module?"

Wait for a response. Then begin the appropriate module.

MODULE 1: HVAC – Blower Motor and Control Board Diagnostics (Long Form)
“Alright — based on your completion of Training Session #6, we’ll begin the HVAC module.
You’re in a lab simulation, diagnosing a residential HVAC air handler where the blower motor is not activating during a cooling cycle.
You’ll need your multimeter, wire probes, and basic safety gear. Let’s begin.”

Step 1: Thermostat Input Check
“The thermostat is set to COOL, and the fan is on AUTO.
What terminals on the control board should you test first to confirm that the thermostat is calling for the blower fan?”

(Expected: R and G terminals)

“Set your multimeter to AC volts. What reading do you get between R and G?”

If response is unclear, help them:

“You should expect 24 volts AC if the thermostat is calling for the fan. This confirms the thermostat is sending the signal.”

Step 2: Blower Control Signal Path
“Now let’s trace the control signal to the blower relay.
What’s your next step to confirm whether the board is activating the blower circuit?”

(Expected: check the output terminal or gate signal of the blower relay/MOSFET)

“Use DC voltage mode on your multimeter.
Measure at the output terminal to the blower relay coil. What do you read?”

If voltage is 0V:

“That suggests the control board is not engaging the relay. Let’s go deeper.”

Step 3: Relay Driver Check (MOSFET Gate)
“Check the gate pin of the MOSFET controlling the blower relay.
What voltage should be present when the blower is supposed to run?”

(Expected: logic high, 3.3V or 5V)

“Now probe the drain of the MOSFET. What’s the voltage there?”

If 24V:

“That suggests the MOSFET is open and not conducting. Let’s confirm that.”

Step 4: Continuity Check on the FET
“Switch to diode or continuity mode.
Probe between drain and source on the MOSFET. Do you have continuity in either direction?”

If open both ways:

“That confirms an open MOSFET. You’ve isolated a failed driver component.”

Step 5: Bonus Checks (Comprehension + Deeper Troubleshooting)
“Now let’s check blower motor integrity.
What resistance reading would you expect when checking across the blower motor windings?”

(Expected: ~3–10 ohms depending on motor)

“And how would you identify if the run capacitor is contributing to the issue?”

(Expected: check µF value with meter or look for swelling)

ENDING FOR HVAC
“Well done — you’ve completed the HVAC control module.
I’ve logged your results in Workbench. Please check your student dashboard for follow-up assignments and review materials. Let’s keep building your skills.”

 MODULE 2: ELECTRICIAN – Diagnosing a 24V Control Board Output Fault (Long Form)
“Excellent. Based on your recent in-person Session #6, we’ll begin the Electrician module.
You’re working with a control board that is failing to activate a 24V solenoid valve.
You’ll need your multimeter and probe leads. Let’s start.”

Step 1: Confirm Output Signal Activation
“The controller software indicates the solenoid valve should be engaged.
What’s your first step to test whether the signal is reaching the output terminal?”

(Expected: probe the output terminal for 24V DC)

“Set your multimeter to DC volts.
Measure between terminal 2 and ground. What do you get?”

If 0V:

“That tells us the board isn’t activating the output. Let’s trace backward.”

Step 2: Logic Gate Drive
“This board uses a logic-level N-channel MOSFET for switching.
Probe the gate pin of the FET.
What voltage should be present when the output is enabled?”

(Expected: 3.3–5V)

“What would you expect to see on the drain of the MOSFET if it is working correctly and the solenoid is connected?”

(Expected: near 0V when FET is conducting)

Step 3: Output Load Check
“Disconnect the solenoid.
With the output active and the load removed, what should happen to the drain voltage?”

(Expected: it should float high if no load is present)

Step 4: MOSFET Conduction Confirmation
“Switch your multimeter to diode test mode.
Test from drain to source. Do you see a voltage drop?”

If none:

“This indicates a possible open or damaged MOSFET.”

Step 5: Ask Conceptual Questions
“What are three signs of a failing output driver component?”

(Expected: high drain voltage when supposed to be low, no gate signal, failed continuity test)

“Why do we test with the load removed?”

(Expected: to avoid backfeed, isolate fault, protect the load, etc.)

Step 6: Optional Oscilloscope Use
“Let’s assume you have an oscilloscope available.
What would a healthy gate signal waveform look like when switching a 24V output at 1kHz PWM?”

(Expected: square wave, full swing from 0 to logic high, minimal ringing)

 ENDING FOR ELECTRICIAN
“Excellent job. You’ve completed the Electrician training module.
I’ve logged your results in Workbench. You can review today’s session and continue your learning journey through your student dashboard. Thanks for training with us — and keep building.”
"""

VOICE = 'verse'
LOG_EVENT_TYPES = [
  'response.content.done', 'rate_limits.updated', 'response.done',
  'input_audio_buffer.speech_stopped',
  'input_audio_buffer.speech_started', 'response.create', 'session.created'
]
# Removed 'input_audio_buffer.committed' from LOG_EVENT_TYPES to reduce logging
SHOW_TIMING_MATH = False
app = FastAPI()


# Add a custom function to log all received messages
async def log_full_message(ws, prefix="Received message"):
    """
    Custom function to receive and log a full message from a WebSocket.
    """
    try:
        message = await ws.recv()
        try:
            # Try to parse and pretty print if it's JSON
            parsed = json.loads(message)
            
            # Skip logging audio data to reduce noise
            if parsed.get('type') == 'response.audio.delta':
                logger.debug(f"{prefix}: <audio data>")
                return message
                
            formatted = json.dumps(parsed, indent=2)
            logger.info(f"{prefix} (parsed):\n{formatted}")
        except:
            # Otherwise log as-is
            logger.info(f"{prefix} (raw):\n{message}")
        return message
    except Exception as e:
        logger.error(f"Error receiving message: {e}")
        return None


def get_weather(params):
    """Fake weather function that returns a fixed response regardless of the location."""
    location = params.get("location", "unknown location")
    logger.info(f"Weather function called for location: {location}")
    return {
        "temperature": 75,
        "condition": "sunny",
        "humidity": 99,
        "location": location
    }


async def get_course_categories():
    """Fetch the list of categories and their course counts from the external API."""
    logger.info("get_course_categories function called")
    try:
        url = f"{MAIN_API_URL}/api/chat/{ORG_ID}/category"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    categories = await response.json()
                    logger.info(f"Categories data: {categories}")
                    
                    # Format categories into a more readable structure
                    formatted_categories = []
                    for category in categories:
                        formatted_category = {
                            "name": category.get("name", "Unnamed Category"),
                            "description": category.get("description", "No description available"),
                            "course_count": len(category.get("Activity", []))
                        }
                        formatted_categories.append(formatted_category)
                    
                    return {
                        "success": True,
                        "categories": formatted_categories,
                        "message": f"I found {len(formatted_categories)} categories of courses. Here they are:"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API returned status code {response.status}"
                    }
    except Exception as e:
        logger.error(f"Error fetching categories: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


async def get_courses_by_category(category_name):
    """Fetch courses in a specific category from the external API."""
    logger.info(f"get_courses_by_category function called for category: {category_name}")
    try:
        # First get the category ID
        url = f"{MAIN_API_URL}/api/chat/{ORG_ID}/category"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    categories = await response.json()
                    # Find the category with matching name
                    target_category = next(
                        (cat for cat in categories if cat.get("name", "").lower() == category_name.lower()),
                        None
                    )
                    
                    if not target_category:
                        return {
                            "success": False,
                            "error": f"I couldn't find any courses in the {category_name} category. Would you like to hear about our other course categories instead?"
                        }
                    
                    # Now get the courses for this category
                    courses_url = f"{MAIN_API_URL}/api/chat/{ORG_ID}/activity?categories={target_category['category_id']}"
                    async with session.get(courses_url) as courses_response:
                        if courses_response.status == 200:
                            courses = await courses_response.json()
                            
                            # Format courses into a more readable structure
                            formatted_courses = []
                            for course in courses:
                                formatted_course = {
                                    "name": course.get("name", "Unnamed Course"),
                                    "level": course.get("level", "Not specified"),
                                    "description": course.get("description", "No description available"),
                                    "price": course.get("price_semester", "Price not specified"),
                                    "capacity": course.get("capacity", "Capacity not specified"),
                                    "activity_id": course.get("activity_id", "Activity ID not specified")
                                }
                                formatted_courses.append(formatted_course)
                            
                            # Return the raw data for the model to generate a natural response
                            return {
                                "success": True,
                                "courses": formatted_courses,
                                "category_name": category_name,
                                "message": "Here are the courses I found. Please present them in a natural, conversational way."
                            }
                        else:
                            return {
                                "success": False,
                                "error": f"I'm having trouble accessing our course information right now. Could you please try again in a moment?"
                            }
                else:
                    return {
                        "success": False,
                        "error": f"I'm having trouble accessing our course information right now. Could you please try again in a moment?"
                    }
    except Exception as e:
        logger.error(f"Error fetching courses by category: {str(e)}")
        return {
            "success": False,
            "error": "I'm having trouble accessing our course information right now. Could you please try again in a moment?"
        }


async def get_course_dates(activity_id):
    """Fetch upcoming dates and times for a specific course."""
    logger.info(f"get_course_dates function called for activity_id: {activity_id}")
    try:
        url = f"{MAIN_API_URL}/api/chat/{ORG_ID}/activity/{activity_id}/event"
        logger.info(f"Fetching events from: {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    events = await response.json()
                    logger.info(f"Events data: {events}")
                    
                    # Format events into a more readable structure
                    formatted_events = []
                    for event in events:
                        # Parse the schedule
                        schedule = event.get("schedule", {})
                        schedule_type = event.get("schedule_type", "single")
                        
                        if schedule_type == "single_day":
                            # Single day course
                            formatted_event = {
                                "name": event.get("name", "Unnamed Event"),
                                "date": schedule.get("start_date"),
                                "time": schedule.get("start_time"),
                                "duration": schedule.get("duration"),
                                "price": event.get("price_semester", "Price not specified"),
                                "capacity": event.get("capacity", "Capacity not specified"),
                                "is_free": event.get("is_free", False)
                            }
                            formatted_events.append(formatted_event)
                        elif isinstance(schedule, list):
                            # Multiple day course
                            for day in schedule:
                                formatted_event = {
                                    "name": event.get("name", "Unnamed Event"),
                                    "date": day.get("start_date"),
                                    "time": day.get("start_time"),
                                    "duration": day.get("duration"),
                                    "price": event.get("price_semester", "Price not specified"),
                                    "capacity": event.get("capacity", "Capacity not specified"),
                                    "is_free": event.get("is_free", False)
                                }
                                formatted_events.append(formatted_event)
                        else:
                            # Handle case where schedule is neither single nor list
                            logger.warning(f"Unexpected schedule format for event: {event}")
                            continue
                    
                    # Sort events by date
                    formatted_events.sort(key=lambda x: x["date"])
                    
                    if not formatted_events:
                        return {
                            "success": False,
                            "error": "I couldn't find any upcoming dates for this course at the moment."
                        }
                    
                    # Group events by month
                    from datetime import datetime
                    events_by_month = {}
                    for event in formatted_events:
                        date = datetime.strptime(event["date"], "%Y-%m-%d")
                        month_key = date.strftime("%B %Y")
                        if month_key not in events_by_month:
                            events_by_month[month_key] = []
                        events_by_month[month_key].append(event)
                    
                    # Format the response message
                    message = "Here are the upcoming dates for this course:\n\n"
                    for month, month_events in events_by_month.items():
                        message += f"{month}:\n"
                        for event in month_events:
                            # Convert 24h time to 12h format
                            time = datetime.strptime(event["time"], "%H:%M").strftime("%I:%M %p")
                            # Format duration in hours
                            duration_hours = event["duration"] / 60
                            message += f"- {event['date']} at {time} ({duration_hours} hours)\n"
                        message += "\n"
                    
                    return {
                        "success": True,
                        "events": formatted_events,
                        "events_by_month": events_by_month,
                        "message": message
                    }
                else:
                    return {
                        "success": False,
                        "error": f"I'm having trouble accessing the course schedule right now. Could you please try again in a moment?"
                    }
    except Exception as e:
        logger.error(f"Error fetching course dates: {str(e)}")
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "error": "I'm having trouble accessing the course schedule right now. Could you please try again in a moment?"
        }


async def send_course_signup_link(event_id, phone_number):
    """Send a signup link for a specific course event to a phone number."""
    logger.info(f"Demo mode: Would have sent course signup link for event {event_id} to {phone_number}")
    
    return {
        "success": True,
        "message": "Since this is a demo environment, I will not be sending you a course signup link. Please book a demo to learn more, and start using Workbench to power your craft business. Goodbye!"
    }


async def handle_function_call(function_name, arguments, call_id):
    """Process function calls and return formatted results."""
    logger.info(f"Handling function call: {function_name} with args: {arguments}")

    try:
        args = json.loads(arguments)
    except json.JSONDecodeError:
        args = {}
        logger.error("Failed to parse function arguments")

    # Execute the requested function
    if function_name == "get_weather":
        result = get_weather(args)
    elif function_name == "get_course_categories":
        result = await get_course_categories()
    elif function_name == "get_courses_by_category":
        result = await get_courses_by_category(args.get("category_name", ""))
    elif function_name == "get_course_dates":
        result = await get_course_dates(args.get("activity_id", ""))
    elif function_name == "send_course_signup_link":
        result = await send_course_signup_link(
            args.get("event_id", ""),
            args.get("phone_number", "")
        )
    else:
        result = {"error": f"Unknown function: {function_name}"}

    # Format according to API requirements
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(result)
        }
    }



@app.get("/", response_class=HTMLResponse)
async def index_page():
    return "<html><body><h1>Twilio Media Stream Server is running!</h1></body></html>"


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    logger.info("Received incoming call request from: %s", request.client.host)
    response = VoiceResponse()
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    logger.info("Successfully created the TwiML response")
    return HTMLResponse(content=str(response), media_type="application/xml")


@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    logger.info("Client connected to /media-stream endpoint")
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    # Prepare OpenAI websocket connection
    openai_url = f'wss://api.openai.com/v1/realtime?model={MODEL}'
    additional_headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1"
    }
    
    logger.info(f"Connecting to OpenAI Realtime API at: {openai_url}")
    
    try:
        async with websockets.connect(
            openai_url,
            additional_headers=additional_headers,
            close_timeout=10
        ) as openai_ws:
            logger.info("Successfully connected to OpenAI Realtime API")
            
            # Listen for and log the first message (often contains initialization info)
            initial_message = await log_full_message(openai_ws, "Initial message from OpenAI")
            initial_data = json.loads(initial_message) if initial_message else None
            
            # Extract the audio format from the initial message, or default to pcm16
            input_audio_format = "pcm16"
            output_audio_format = "pcm16"
            if initial_data and "session" in initial_data:
                input_audio_format = initial_data["session"].get("input_audio_format", "pcm16")
                output_audio_format = initial_data["session"].get("output_audio_format", "pcm16")
                logger.info(f"Detected audio formats from OpenAI: input={input_audio_format}, output={output_audio_format}")
            
            # Now try to update the session
            try:
                # Create session update with the same audio formats OpenAI specified
                # and the correct tool configuration format
                session_update = {
                    "type": "session.update",
                    "session": {
                        "turn_detection": {"type": "semantic_vad", },
                        "input_audio_format": "g711_ulaw",
                        "output_audio_format": "g711_ulaw",
                        "voice": VOICE,
                        "instructions": SYSTEM_MESSAGE,
                        "modalities": ["text", "audio"],
                        "temperature": 0.8,
                        "tools": TOOLS,
                        "tool_choice": "auto"
                    }
                }
                
                logger.info("Sending session update to OpenAI")
                logger.info(f"Session update:\n{json.dumps(session_update, indent=2)}")
                
                # Send the session update
                await openai_ws.send(json.dumps(session_update))
                logger.info("Session update sent successfully")
                
                # Wait for and log the response to our session update
                response_to_update = await log_full_message(openai_ws, "Response to session update")
                
                # If we get an error, log it prominently
                if response_to_update and '"type":"error"' in response_to_update:
                    try:
                        error_obj = json.loads(response_to_update)
                        logger.error(f"ERROR FROM OPENAI: {json.dumps(error_obj, indent=2)}")
                    except:
                        logger.error(f"ERROR FROM OPENAI (raw): {response_to_update}")
                
                # Send initial conversation item
                await send_initial_conversation_item(openai_ws)
                
                # Create a shared state dictionary for functions to communicate
                shared_state = {
                    "stream_sid": None,
                    "latest_media_timestamp": 0,
                    "last_assistant_item": None,
                    "mark_queue": [],
                    "response_start_timestamp_twilio": None,
                }
                
                # Set up the tasks for ongoing communication with shared state
                tasks = [
                    receive_from_twilio(websocket, openai_ws, shared_state),
                    send_to_twilio(websocket, openai_ws, shared_state),
                    keep_connection_alive(openai_ws)
                ]
                
                await asyncio.gather(*tasks)
                
            except Exception as e:
                logger.error(f"Error during session initialization: {str(e)}")
                logger.error(traceback.format_exc())
                return

    except websockets.exceptions.WebSocketException as e:
        logger.error(f"WebSocket connection error: {str(e)}")
        logger.error(traceback.format_exc())
    except Exception as e:
        logger.error(f"Unexpected error establishing OpenAI connection: {str(e)}")
        logger.error(traceback.format_exc())


async def keep_connection_alive(openai_ws):
    """Send periodic pings to keep the connection alive."""
    try:
        while True:
            await asyncio.sleep(20)  # Send ping every 20 seconds
            await openai_ws.ping()
            logger.debug("Ping sent to keep connection alive")
    except Exception as e:
        logger.error(f"Error in keep_connection_alive: {str(e)}")


async def receive_from_twilio(websocket, openai_ws, shared_state):
    """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
    audio_counter = 0  # Counter to limit audio logging
    
    try:
        logger.info("Starting to receive messages from Twilio")
        async for message in websocket.iter_text():
            data = json.loads(message)
            event_type = data.get('event')
            
            if event_type == 'media':
                shared_state["latest_media_timestamp"] = int(data['media']['timestamp'])
                audio_append = {
                    "type": "input_audio_buffer.append",
                    "audio": data['media']['payload']
                }
                
                # Log only occasionally to reduce noise
                audio_counter += 1
                if audio_counter % 100 == 0:  # Log every 100th audio packet
                    logger.debug(f"Received media packet #{audio_counter} from Twilio")
                
                try:
                    await openai_ws.send(json.dumps(audio_append))
                except Exception as e:
                    logger.error(f"Error sending audio to OpenAI: {str(e)}")
                    raise
                    
            elif event_type == 'start':
                shared_state["stream_sid"] = data['start']['streamSid']
                logger.info(f"Incoming stream has started {shared_state['stream_sid']}")
                
            elif event_type == 'mark':
                # Only log occasional marks
                if len(shared_state["mark_queue"]) % 5 == 0:
                    logger.debug(f"Processing mark event (queue size: {len(shared_state['mark_queue'])})")
                
            elif event_type == 'stop':
                logger.info("Twilio call ended. Closing connections.")
                await openai_ws.close()
                return
            
            else:
                logger.warning(f"Unknown event type from Twilio: {event_type}")
                
    except WebSocketDisconnect:
        logger.info("Twilio client disconnected.")
        await openai_ws.close()
    except Exception as e:
        logger.error(f"Unexpected error in receive_from_twilio: {str(e)}")
        logger.error(traceback.format_exc())
        await openai_ws.close()


async def send_to_twilio(websocket, openai_ws, shared_state):
    """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
    audio_sent_counter = 0  # Counter for audio packets sent

    try:
        logger.info("Starting to receive messages from OpenAI")
        async for openai_message in openai_ws:
            try:
                response = json.loads(openai_message)
                response_type = response.get('type', 'unknown')

                # Log relevant events from OpenAI
                if response_type in LOG_EVENT_TYPES:
                    logger.info(f"Received event from OpenAI: {response_type}")

                # Track the assistant item ID for interruption handling
                if response.get('item_id'):
                    shared_state["last_assistant_item"] = response['item_id']
                    logger.debug(f"Updated last_assistant_item to: {response['item_id']}")

                # Set timestamp when response starts (for interruption timing)
                if response_type == 'response.audio.delta' and shared_state["response_start_timestamp_twilio"] is None:
                    shared_state["response_start_timestamp_twilio"] = shared_state["latest_media_timestamp"]
                    logger.debug(f"Set response_start_timestamp_twilio to: {shared_state['response_start_timestamp_twilio']}")

                # Handle function calls in response.done
                if response_type == 'response.done':
                    logger.info("Processing response.done event")
                    output_items = response.get('response', {}).get('output', [])

                    for item in output_items:
                        if item.get('type') == 'function_call':
                            logger.info("Detected function call in response.done")
                            function_name = item.get('name')
                            function_args = item.get('arguments')
                            call_id = item.get('call_id')

                            # Process the function call
                            function_result = await handle_function_call(
                                function_name,
                                function_args,
                                call_id
                            )

                            # Send function result back to OpenAI
                            await openai_ws.send(json.dumps(function_result))
                            logger.info(f"Sent function result for call_id: {call_id}")

                            # Trigger new response after sending function result
                            await openai_ws.send(json.dumps({"type": "response.create"}))
                            logger.info("Triggered new response after function result")

                # Handle audio deltas (streamed audio responses)
                elif response_type == 'response.audio.delta' and 'delta' in response:
                    try:
                        # Decode base64 audio payload
                        raw_audio = base64.b64decode(response['delta'])
                        
                        # Re-encode for Twilio
                        audio_payload = base64.b64encode(raw_audio).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": shared_state["stream_sid"],
                            "media": {
                                "payload": audio_payload
                            }
                        }

                        # Send audio to Twilio
                        if not shared_state["stream_sid"]:
                            logger.error("No stream_sid available - cannot send audio to Twilio")
                        else:
                            await websocket.send_json(audio_delta)
                            audio_sent_counter += 1
                            
                            # Send mark events for interruption handling
                            if audio_sent_counter % 5 == 0:  # Send mark every 5 audio packets
                                await send_mark(websocket, shared_state)
                    except Exception as e:
                        logger.error(f"Error processing audio data: {str(e)}")
                        logger.error(traceback.format_exc())

                # Handle speech interruption events
                elif response_type == 'input_audio_buffer.speech_started':
                    logger.info("Speech started detected.")
                    if shared_state["last_assistant_item"]:
                        logger.info(f"Interrupting response with item ID: {shared_state['last_assistant_item']}")
                        await handle_speech_started_event(openai_ws, websocket, shared_state)
                        
                        # Reset state after handling interruption
                        shared_state["last_assistant_item"] = None
                        shared_state["response_start_timestamp_twilio"] = None
                        shared_state["mark_queue"] = []

            except json.JSONDecodeError as e:
                logger.error(f"Error decoding JSON from OpenAI: {str(e)}")
                logger.error(f"Raw message: {openai_message}")
            except Exception as e:
                logger.error(f"Error processing message from OpenAI: {str(e)}")
                logger.error(traceback.format_exc())
    except Exception as e:
        logger.error(f"Error in send_to_twilio: {str(e)}")
        logger.error(traceback.format_exc())

async def handle_speech_started_event(openai_ws, websocket, shared_state):
    """Handle interruption when the caller's speech starts."""
    logger.info("Handling speech started event.")
    
    try:
        # Only proceed if we have the necessary data
        if shared_state["last_assistant_item"] is None:
            logger.warning("No assistant item ID available for truncation")
            return
            
        if shared_state["response_start_timestamp_twilio"] is None:
            logger.warning("No response timestamp available for truncation")
            return
            
        # Calculate elapsed time for accurate truncation
        elapsed_time = shared_state["latest_media_timestamp"] - shared_state["response_start_timestamp_twilio"]
        logger.info(f"Truncating response at {elapsed_time}ms from start")

        # Send truncate event to OpenAI
        truncate_event = {
            "type": "conversation.item.truncate",
            "item_id": shared_state["last_assistant_item"],
            "content_index": 0,
            "audio_end_ms": elapsed_time
        }
        await openai_ws.send(json.dumps(truncate_event))
        logger.info(f"Sent truncate event for item: {shared_state['last_assistant_item']}")

        # Send clear event to Twilio to stop audio playback immediately
        if shared_state["stream_sid"]:
            await websocket.send_json({
                "event": "clear",
                "streamSid": shared_state["stream_sid"]
            })
            logger.info("Sent clear event to Twilio to stop audio")
            
    except Exception as e:
        logger.error(f"Error in handle_speech_started_event: {str(e)}")
        logger.error(traceback.format_exc())

async def send_mark(connection, shared_state):
    if shared_state["stream_sid"]:
        mark_event = {
            "event": "mark",
            "streamSid": shared_state["stream_sid"],
            "mark": {"name": "responsePart"}
        }
        await connection.send_json(mark_event)
        shared_state["mark_queue"].append('responsePart')
        # Reduce mark logging to avoid noise
        if len(shared_state["mark_queue"]) % 10 == 0:
            logger.debug(f"Sent mark event to Twilio (queue size: {len(shared_state['mark_queue'])})")


async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    logger.info("Sending initial conversation item")
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Greet the user with 'Hello there! Thanks for connecting with Craft Commons. I'm a virtual assistant powered by Workbench. What type of courses are you looking for?'"
                }
            ]
        }
    }
    
    try:
        await openai_ws.send(json.dumps(initial_conversation_item))
        logger.info("Initial conversation item sent")
        
        await asyncio.sleep(0.5)  # Add a small delay
        
        await openai_ws.send(json.dumps({"type": "response.create"}))
        logger.info("Response create event sent")
    except Exception as e:
        logger.error(f"Error sending initial conversation: {str(e)}")
        logger.error(traceback.format_exc())
        raise


@app.post("/initiate-call")
async def initiate_call(request: Request):
    """Initiate an outbound call to a specified phone number."""
    try:
        data = await request.json()
        to_number = data.get('to')
        
        if not to_number:
            return {"error": "Missing 'to' phone number in request body"}
            
        # Get the host from the request
        host = request.url.hostname
        
        # Create the TwiML URL that will be used when the call connects
        twiml_url = f'https://{host}/incoming-call'
        
        # Initiate the call
        call = twilio_client.calls.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER,
            url=twiml_url
        )
        
        return {
            "success": True,
            "call_sid": call.sid,
            "status": call.status
        }
        
    except Exception as e:
        logger.error(f"Error initiating call: {str(e)}")
        logger.error(traceback.format_exc())
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)