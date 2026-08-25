# 파일명: camera_server.py (맥북에서 실행)
from flask import Flask, Response
import cv2

app = Flask(__name__)
# 내장 카메라 로드 (해상도를 OCR 최적 규격인 640x480으로 조절하여 속도 확보)
camera = cv2.VideoCapture(0)
camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

def generate_frames():
    while True:
        success, frame = camera.read()
        if not success:
            break
        else:
            # 실시간 프레임을 JPEG 포맷으로 인코딩
            ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            frame_bytes = buffer.tobytes()
            
            # MJPEG 스트리밍 규격에 맞게 yield 송출
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/video')
def video_feed():
    """라즈베리파이가 접속할 비디오 스트리밍 엔드포인트"""
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    return "<h1>MacBook Camera Stream Server is Running!</h1><p>Go to <a href='/video'>/video</a></p>"

if __name__ == '__main__':
    # 0.0.0.0으로 열어야 같은 와이파이 안의 라즈베리파이가 접속할 수 있습니다.
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)