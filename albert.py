import os
import time
import json
import queue
import threading
import cv2
import sounddevice as sd
from flask import Flask, Response, render_template_string
from flask_socketio import SocketIO
from vosk import Model, KaldiRecognizer
import google.generativeai as genai
from gtts import gTTS
from playsound import playsound

# --- НАЛАШТУВАННЯ ---
try:
    from config import GEMINI_API_KEY
except ImportError:
    print("Помилка: Файл config.py не знайдено!")
    GEMINI_API_KEY = ""
genai.configure(api_key=GEMINI_API_KEY.strip())
gemini_model = genai.GenerativeModel('gemini-2.5-flash')

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

camera = cv2.VideoCapture(0, cv2.CAP_DSHOW) # Додано CAP_DSHOW для Windows

if not os.path.exists("model"):
    print("Помилка: Папка 'model' не знайдена!")
    exit()

vosk_model = Model("model")
samplerate = 16000 
rec = KaldiRecognizer(vosk_model, samplerate)
audio_queue = queue.Queue()

is_waiting_for_question = False

# --- ФУНКЦІЇ ЗВУКУ ---

def play_audio(filename):
    try:
        playsound(filename, block=True)
    except Exception as e:
        print(f"Помилка відтворення: {e}")

def speak_ukrainian(text):
    try:
        tts = gTTS(text=text, lang='uk')
        filename = f"temp_res.mp3" # Спрощено для Windows
        tts.save(filename)
        play_audio(filename)
        if os.path.exists(filename):
            os.remove(filename)
    except Exception as e:
        print(f"Помилка TTS: {e}")

# --- ФУНКЦІЯ ВИМКНЕННЯ ---

def shutdown_albert():
    print(">>> Отримано команду на вимкнення. Прощавайте!")
    speak_ukrainian("До побачення! Лягаю спати.")
    camera.release()
    os._exit(0) # Повне завершення скрипта

# --- ЛОГІКА РОБОТИ ---

def ask_gemini(question):
    try:
        prompt = f"Відповідай дуже коротко (до 15 слів) українською мовою: {question}"
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Помилка Gemini: {e}"

def audio_callback(indata, frames, time, status):
    audio_queue.put(bytes(indata))

def albert_logic():
    global is_waiting_for_question
    
    with sd.RawInputStream(samplerate=samplerate, blocksize=4000, device=None,
                            dtype='int16', channels=1, callback=audio_callback):
        print("Альберт готовий. Фрази: 'Альберт питання', 'Альберт спать', 'Стоп'")
        
        while True:
            data = audio_queue.get()
            
            # Перевірка PartialResult для швидких команд
            partial = json.loads(rec.PartialResult())
            p_text = partial.get('partial', '').lower()
            
            if "альберт стоп" in p_text:
                print(">>> Зупинка звуку")
                # playsound важко зупинити, але ми скидаємо розпізнавач
                rec.Reset()
                continue
            
            # КОМАНДА НА ВИМКНЕННЯ
            if "альберт спать" in p_text:
                shutdown_albert()

            if rec.AcceptWaveform(data):
                result = json.loads(rec.Result())
                text = result.get('text', '').lower()
                
                if not is_waiting_for_question:
                    if "альберт питання" in text:
                        play_audio("notify.mp3")
                        is_waiting_for_question = True
                        socketio.emit('status', {'msg': 'Слухаю вас...'})
                        rec.Reset()
                else:
                    if len(text.strip()) > 2:
                        socketio.emit('status', {'msg': 'Обробка...'})
                        answer = ask_gemini(text)
                        socketio.emit('chat_answer', {'answer': answer})
                        speak_ukrainian(answer)
                        
                        is_waiting_for_question = False
                        socketio.emit('status', {'msg': 'Чекаю на "Альберт питання"'})
                        rec.Reset()

# --- ВЕБ-ЧАСТИНА ---

def generate_frames():
    while True:
        success, frame = camera.read()
        if not success: break
        ret, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route('/')
def index():
    return render_template_string("""
    <html>
        <head>
            <title>Albert System</title>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
            <style>
                body { background: #121212; color: white; font-family: sans-serif; text-align: center; }
                #status { font-size: 22px; color: #00ff00; margin: 15px; }
                .video-box { border: 2px solid #444; display: inline-block; }
            </style>
        </head>
        <body>
            <h1>Albert System</h1>
            <div id="status">Запуск...</div>
            <div class="video-box"><img src="/video_feed" width="640"></div>
            <div id="answer" style="margin-top:20px; padding:10px; background:#1e1e1e;"></div>
            <script>
                var socket = io();
                socket.on('status', function(data) { document.getElementById('status').innerText = data.msg; });
                socket.on('chat_answer', function(data) { document.getElementById('answer').innerText = data.answer; });
            </script>
        </body>
    </html>
    """)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == "__main__":
    threading.Thread(target=albert_logic, daemon=True).start()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)