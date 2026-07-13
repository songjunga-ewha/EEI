import os
import cv2
import time
from google import genai
from google.genai import types

# =======================================================
# [API 세팅] 구글 AI 스튜디오에서 발급받은 API 키를 입력하세요.
# =======================================================
GEMINI_API_KEY = "여기에_복사한_키_붙여넣기"

# Gemini 클라이언트 초기화
client = genai.Client(api_key=GEMINI_API_KEY)


def analyze_image_with_gemini(image_path):
    """
    [딥러닝 B 최첨단 고도화 로직]
    로컬 OCR 엔진을 과감히 버리고, 초거대 AI인 Gemini 멀티모달 비전 모델을 활용하여
    어떤 형태/조명/종류의 영양성분표에서도 '당류 수치'만 정밀 저격해 추출합니다.
    """
    start_time = time.time()
    print("\n---------------------------------------------------")
    print("🚀 [Gemini AI 가동] 구글 클라우드 멀티모달 비전 연산 시작...")
    print("---------------------------------------------------")
    
    if not os.path.exists(image_path):
        print("❌ [에러] 분석할 이미지가 존재하지 않습니다.")
        return

    try:
        # 1. 캡처된 사진 파일 읽기
        with open(image_path, "rb") as f:
            image_bytes = f.read()
            
        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type="image/jpeg",
        )

        # 2. Gemini에게 던질 정교한 '프롬프트 지시문' 작성 (Zero-shot 정밀 타겟팅)
        prompt = """
        너는 음료수 영양성분표 전문 분석 AI 에이전트야.
        주어진 이미지에서 '당류(Sugars)'의 함량 수치만 찾아서 숫자와 단위(g)만 깔끔하게 답변해줘.
        
        [출력 규칙]
        1. 이미지에 한글이 깨졌거나 흐려도 문맥상 '당류'에 해당하는 값을 사람처럼 유추해서 찾아내야 해.
        2. 오직 결과값만 출력해줘. 예: 7g, 12g, 0g
        3. 만약 진짜로 당류를 찾을 수 없다면 '미정'이라고만 답해줘. 다른 군더더기 설명은 절대 하지마.
        """

        print("▶ [Gemini] 이미지 패킷 전송 중...")
        
        # 3. Gemini 2.5 Flash 모델 호출 (비전 인식 속도가 가장 빠르고 정확함)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[image_part, prompt]
        )
        
        # 4. 결과값 공백 제거 및 정제
        result_text = response.text.strip()
        
        print("\n================== [Gemini AI 인식 결과] ==================")
        print(f"🤖 구글 제미나이 응답 원문: {result_text}")
        print("===========================================================\n")
        
        print("================== [성분 매핑 최종 결과] ==================")
        if "미정" in result_text or not result_text:
            print("⚠️ Gemini AI가 이미지에서 당류 수치를 특정하지 못했습니다. 다시 촬영해 주세요.")
        else:
            print(f"🏆 [범용 당류 포착 성공] -> 최종 당류 수치: {result_text}")
        print("=======================================================")

    except Exception as e:
        print(f"❌ [Gemini API 에러 발생]: {e}")
        print("💡 API 키가 정상적으로 입력되었는지, 또는 인터넷 연결을 확인해 주세요.")
        
    print(f"⚡ [분석 완료] 총 소요 시간: {time.time() - start_time:.2f}초\n")


def run_camera():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ 카메라 장치를 열 수 없습니다.")
        return

    print("\n=== 📸 Gemini AI 탑재 범용 영양성분표 트래커 ===")
    print("- Spacebar: 현재 화면 캡처 후 구글 Gemini AI로 당류 즉시 분석")
    print("- Esc: 프로그램 안전 종료")
    print("==================================================")

    while True:
        ret, frame = cap.read()
        if not ret: 
            break
            
        cv2.imshow('Webcam', frame)
        key = cv2.waitKey(1) & 0xFF

        # 스페이스바를 누르면 웹캠 화면을 찍어서 Gemini로 패스!
        if key == 32 or key == ord(' '):
            filename = "gemini_capture.jpg"
            cv2.imwrite(filename, frame)
            print(f"\n[알림] 현재 화면 포착 완료! ('{filename}')")
            analyze_image_with_gemini(filename)

        elif key == 27:
            print("[알림] 프로그램을 정상 종료합니다.")
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    run_camera()