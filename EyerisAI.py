# EyerisAI.py   
import os
import time
from datetime import datetime
import cv2
import pyttsx3
import numpy as np
from ollama import Client
import configparser
import json
from pathlib import Path
import smtplib
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart

def load_config():
    """
    Load configuration from config.ini
    """
    config = configparser.ConfigParser()
    config.read('config.ini')
    
    # Parse the contour color from string
    contour_color = tuple(map(int, config.get('Visualization', 'contour_color').split(',')))
    timestamp_color = tuple(map(int, config.get('Visualization', 'timestamp_color').split(',')))
    
    return {
        'save_directory': config.get('General', 'save_directory'),
        'log_file': config.get('General', 'log_file'),
        'ai_description': config.getboolean('General', 'ai_description'),
        'instance_name': config.get('General', 'instance_name', fallback='Motion Detector'),
        'ai': {
            'api_type': config.get('AI', 'api_type'),  # 'ollama' or 'openai'
            'base_url': config.get('AI', 'base_url'),
            'model': config.get('AI', 'model'),
            'prompt': config.get('AI', 'prompt'),
            'api_key': config.get('AI', 'api_key', fallback=None),  # Optional for Ollama
            'max_tokens': config.getint('AI', 'max_tokens', fallback=300)
        },
        'camera': {
            'device_id': config.getint('Camera', 'device_id'),
            'width': config.getint('Camera', 'width'),
            'height': config.getint('Camera', 'height'),
            'auto_exposure': config.getfloat('Camera', 'auto_exposure'),
        },
        'motion_detection': {
            'min_area': config.getint('MotionDetection', 'min_area'),
            'blur_size': (
                config.getint('MotionDetection', 'blur_size_x'),
                config.getint('MotionDetection', 'blur_size_y')
            ),
            'threshold': config.getint('MotionDetection', 'threshold'),
            'cooldown': config.getint('MotionDetection', 'cooldown'),
        },
        'tts': {
            'enabled': config.getboolean('TTS', 'enabled'),
            'rate': config.getint('TTS', 'rate'),
            'volume': config.getfloat('TTS', 'volume')
        },
        'visualization': {
            'draw_contours': config.getboolean('Visualization', 'draw_contours'),
            'contour_color': contour_color,
            'contour_thickness': config.getint('Visualization', 'contour_thickness'),
            'draw_timestamp': config.getboolean('Visualization', 'draw_timestamp'),
            'timestamp_color': timestamp_color 
        },
        'email': {
            'enabled': config.getboolean('Email', 'enabled', fallback=False),
            'smtp_server': config.get('Email', 'smtp_server', fallback=''),
            'smtp_port': config.getint('Email', 'smtp_port', fallback=25),
            'smtp_username': config.get('Email', 'smtp_username', fallback=''),
            'smtp_password': config.get('Email', 'smtp_password', fallback=''),
            'from_address': config.get('Email', 'from_address', fallback=''),
            'to_address': config.get('Email', 'to_address', fallback=''),
            'use_tls': config.getboolean('Email', 'use_tls', fallback=True)
        }
    }

# Load configuration at startup
CONFIG = load_config()

def adjust_camera_settings(cap):
    """
    Adjust camera settings based on configuration
    """
    print("Adjusting camera settings...")
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, CONFIG['camera']['auto_exposure'])
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CONFIG['camera']['width'])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CONFIG['camera']['height'])
    print(f"Camera resolution: {cap.get(cv2.CAP_PROP_FRAME_WIDTH)}x{cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}")
    
    # Capture initial frames to allow camera to adjust exposure
    ret_val, _ = cap.read()
    ret_val, _ = cap.read()

def tts(text):
    """
    Convert text to speech using configured TTS engine
    """
    engine = pyttsx3.init()
    engine.setProperty('rate', CONFIG['tts']['rate'])
    engine.setProperty('volume', CONFIG['tts']['volume'])
    engine.say(text)
    engine.runAndWait()

def describe_image(image) -> str:
    """
    Describe image using configured AI service (Ollama or OpenAI-compatible API)
    """
    api_config = CONFIG['ai']
    
    if api_config['api_type'] == 'ollama':
        # Use direct Ollama client
        ollama_c = Client(host=api_config['base_url'])
        stream = ollama_c.generate(
            model=api_config['model'],
            prompt=api_config['prompt'],
            images=[image],
            stream=True
        )
        response = ""
        for chunk in stream:
            response += chunk['response']
        return response
    
    elif api_config['api_type'] == 'openai':
        import requests
        import base64
        
        # Convert image to base64
        image_b64 = base64.b64encode(image).decode('utf-8')
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {api_config['api_key']}"
        }
        
        payload = {
            'model': api_config['model'],
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': api_config['prompt']},
                        {'type': 'image_url', 
                         'image_url': {
                             'url': f"data:image/jpeg;base64,{image_b64}"
                         }
                        }
                    ]
                }
            ],
            'max_tokens': api_config.get('max_tokens', 300)
        }
        
        response = requests.post(
            f"{api_config['base_url']}/v1/chat/completions",
            headers=headers,
            json=payload
        )
        
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            raise Exception(f"API request failed: {response.text}")
    
    else:
        raise ValueError(f"Unsupported API type: {api_config['api_type']}")

def detect_motion(frame1, frame2):
    """
    Detect motion between two frames and return (motion_detected, contours)
    """
    # Convert frames to grayscale
    gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
    
    # Apply Gaussian blur
    gray1 = cv2.GaussianBlur(gray1, CONFIG['motion_detection']['blur_size'], 0)
    gray2 = cv2.GaussianBlur(gray2, CONFIG['motion_detection']['blur_size'], 0)
    
    # Calculate difference between frames
    frame_diff = cv2.absdiff(gray1, gray2)
    
    # Apply threshold
    thresh = cv2.threshold(frame_diff, CONFIG['motion_detection']['threshold'], 255, cv2.THRESH_BINARY)[1]
    
    # Dilate to fill in holes
    thresh = cv2.dilate(thresh, None, iterations=2)
    
    # Find contours
    contours, _ = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # Filter contours by size
    significant_contours = [cnt for cnt in contours if cv2.contourArea(cnt) > CONFIG['motion_detection']['min_area']]
    
    return len(significant_contours) > 0, significant_contours

def draw_detection_info(frame, contours, timestamp):
    """
    Draw motion detection visualization on the frame
    """
    vis_config = CONFIG['visualization']
    annotated_frame = frame.copy()
    
    if vis_config['draw_contours']:
        # Draw all contours
        cv2.drawContours(
            annotated_frame, 
            contours, 
            -1,  # -1 means draw all contours
            vis_config['contour_color'],
            vis_config['contour_thickness']
        )
    
    if vis_config['draw_timestamp']:
        # Add timestamp to the image
        cv2.putText(
            annotated_frame,
            timestamp,
            (10, 30),  # Position (x, y) from top-left
            cv2.FONT_HERSHEY_SIMPLEX,
            1,  # Font scale
            vis_config['timestamp_color'],
            vis_config['contour_thickness'],
            cv2.LINE_AA
        )
    
    return annotated_frame

def log_event(image_path, description):
    """
    Log event details in JSONL format
    """
    log_file = CONFIG['save_directory'] + '/' + CONFIG['log_file']
    event = {
        'timestamp': datetime.now().isoformat(),
        'image_path': str(image_path),
        'description': description,
        'camera': {
            'id': CONFIG['camera']['device_id'],
            'resolution': f"{CONFIG['camera']['width']}x{CONFIG['camera']['height']}"
        },
        'motion_detection': {
            'min_area': CONFIG['motion_detection']['min_area'],
            'threshold': CONFIG['motion_detection']['threshold']
        },
        'model': CONFIG['ai']['model']
    }
    
    with open(log_file, 'a', encoding='utf-8') as f:
        json.dump(event, f, ensure_ascii=False)
        f.write('\n')
    
    return event

def send_email_alert(image_path, description, timestamp):
    """
    Send email alert with image and description
    """
    if not CONFIG['email']['enabled']:
        return

    email_config = CONFIG['email']
    
    # Create the email message
    msg = MIMEMultipart()
    msg['Subject'] = f"{CONFIG['instance_name']} - Motion Detected at {timestamp}"
    msg['From'] = email_config['from_address']
    msg['To'] = email_config['to_address']

    # Add description as text
    text = MIMEText(f"Motion Detection Alert\n\nTime: {timestamp}\n\nDescription: {description}")
    msg.attach(text)

    # Add image attachment
    with open(image_path, 'rb') as f:
        img = MIMEImage(f.read())
        img.add_header('Content-Disposition', 'attachment', filename=os.path.basename(image_path))
        msg.attach(img)

    # Send the email
    try:
        with smtplib.SMTP(email_config['smtp_server'], email_config['smtp_port']) as server:
            if email_config['use_tls']:
                server.starttls()
            
            if email_config['smtp_username'] and email_config['smtp_password']:
                server.login(email_config['smtp_username'], email_config['smtp_password'])
            
            server.send_message(msg)
            print(f"Email alert sent to {email_config['to_address']}")
    except Exception as e:
        print(f"Failed to send email alert: {str(e)}")

def run_motion_detection():
    """
    Main function to run motion detection
    """
    # Ensure the save directory exists
    save_dir = Path(CONFIG['save_directory'])
    save_dir.mkdir(exist_ok=True)

    # Access the webcam
    cap = cv2.VideoCapture(CONFIG['camera']['device_id'])
    adjust_camera_settings(cap)

    if not cap.isOpened():
        raise IOError("Cannot open webcam")

    # Read two initial frames
    _, frame1 = cap.read()
    last_detection_time = 0

    print("Motion detection started. Press Ctrl+C to stop.")
    
    try:
        while True:
            # Read current frame
            _, frame2 = cap.read()
            
            current_time = time.time()
            motion_detected, contours = detect_motion(frame1, frame2)
            
            if motion_detected:
                # Check if enough time has passed since last detection
                if current_time - last_detection_time > CONFIG['motion_detection']['cooldown']:
                    print("Motion detected!")
                    
                    # Create filename and path
                    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                    filename = f"capture_{timestamp}.png"
                    image_path = save_dir / filename

                    # Draw detection information on the frame
                    annotated_frame = draw_detection_info(frame2, contours, timestamp)

                    # Save the annotated image
                    cv2.imwrite(str(image_path), annotated_frame)
                    im = open(image_path, 'rb').read()
                    if CONFIG['ai_description']:
                        description = describe_image(im)
                    else:
                        description = "Motion detected"

                    # Log the event
                    event = log_event(image_path, description)
                    
                    if CONFIG['ai_description']:
                        print(json.dumps(event, indent=2))
                    
                    if CONFIG['tts']['enabled']:
                        tts(description)
                    
                    # Send email alert if enabled
                    send_email_alert(str(image_path), description, timestamp)
                    
                    last_detection_time = current_time
            
            # Update frame1
            frame1 = frame2.copy()
            
            # Small delay to prevent high CPU usage
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("\nStopping motion detection.")
    finally:
        cap.release()

if __name__ == "__main__":
    run_motion_detection()
