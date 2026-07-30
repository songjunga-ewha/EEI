import os
import ssl
import cv2
import easyocr
import re
import time
import numpy as np

# [안전장치] 맥북 환경 SSL 다운로드 차단 현상 방지
ssl._create_default_https_context = ssl._create_unverified_context

if not os.path.exists('test_samples'):
    os.makedirs('test_samples')

print("⚙️ [시스템] EasyOCR 모델을 로딩 중입니다...")
reader = easyocr.Reader(['ko', 'en'], gpu=False)
print("✅ [시스템] AI 엔진 로드 완료!")


def extract_sugar_with_spatial_filter(valid_results, sugar_idx):
    """
    당류 키워드의 위치를 기준으로 동일한 세로 선상(y축)에 있으면서,
    동시에 지나치게 멀리 떨어지지 않은(x축 비율 영역 제외) 실제 함량 수치를 추출합니다.
    """
    sugar_box, _, sugar_fixed = valid_results[sugar_idx]
    
    sugar_x_right = sugar_box[1][0]
    sugar_y_center = (sugar_box[0][1] + sugar_box[2][1]) / 2
    sugar_height = sugar_box[2][1] - sugar_box[0][1]
    
    y_tolerance = sugar_height * 0.8  # 유연성을 위해 80%로 살짝 조정
    x_max_distance = sugar_height * 6
    
    best_match_num = None
    
    for i, (box, original_text, fixed_text) in enumerate(valid_results):
        if i == sugar_idx:
            continue
            
        current_x_left = box[0][0]
        current_y_center = (box[0][1] + box[2][1]) / 2
        
        if abs(sugar_y_center - current_y_center) <= y_tolerance:
            x_distance = current_x_left - sugar_x_right
            
            if x_distance < -20 or x_distance > x_max_distance:
                continue
                
            match_g = re.search(r'(\d+)\s*(?:g|9|그램)?', fixed_text)
            if match_g:
                num_str = match_g.group(1)
                
                if len(num_str) >= 2 and fixed_text.endswith('9') and not fixed_text.endswith('g'):
                    if '9g' not in fixed_text.lower():
                        num_str = num_str[:-1]
                
                if num_str:
                    best_match_num = f"{int(num_str)}g"
                    break
                    
    if not best_match_num:
        search_range = range(sugar_idx, min(sugar_idx + 3, len(valid_results)))
        for idx in search_range:
            text = valid_results[idx][2]
            match_pure = re.search(r'\d+', text)
            if match_pure:
                best_match_num = f"{int(match_pure.group())}g"
                break
                
    return best_match_num


def process_ocr_analysis(image_path, ocr_reader):
    """
    이미지를 전처리(확대 및 선명화) 후 분석하여 당류, 비타민A/D, 카페인 수치를 반환합니다.
    """
    start_time = time.time()
    print("\n---------------------------------------------------")
    print("⏳ [AI 가동] 3대 핵심 성분 분석 시작 (이미지 필터 가동)...")
    print("---------------------------------------------------")
    
    img = cv2.imread(image_path)
    if img is None:
        print("❌ [에러] 분석할 이미지를 찾을 수 없습니다.")
        return None

    # 🔥 [고도화 필터] 글자 뭉개짐 방지를 위한 이미지 전처리
    # 1. 이미지를 2배 확대하여 글자 간의 간격(0과 g 사이)을 물리적으로 벌려줌
    resized_img = cv2.resize(img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    
    # 2. 그레이스케일 변환 및 대비(Contrast) 향상으로 흐릿한 '0'을 선명하게 만듦
    gray = cv2.cvtColor(resized_img, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.CLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(gray)
    
    # EasyOCR은 흑백 보정 이미지에서 글자 분리 능력이 극대화됩니다.
    processed_img = enhanced

    results = ocr_reader.readtext(
        processed_img, paragraph=False, decoder='beamsearch', beamWidth=7,
        allowlist='당류탄수화물단백질지방나트륨비타민카페인영양성분정보기준치0123456789g%mlLADad ,_`탕뉴량규유슈'
    )

    valid_results = []
    for res in results:
        box = res[0]
        original_text = res[1]
        fixed_text = res[1].replace(" ", "")
        valid_results.append((box, original_text, fixed_text))

    print("================== [성분 매핑 결과] ==================")
    
    sugar_val = "미정"
    vitamins = []
    caffeine_val = "0mg 또는 없음"

    sugar_keywords = ['당류', '당뉴', '탕류', '당규', '량류', '당유', '탄수화물당']
    carbo_keywords = ['탄수화물', '탄수', '탄슈', '탄수화']

    sugar_index = -1
    for i, (box, original_text, fixed_text) in enumerate(valid_results):
        if any(k in fixed_text for k in sugar_keywords):
            sugar_index = i
            break

    if sugar_index != -1:
        extracted = extract_sugar_with_spatial_filter(valid_results, sugar_index)
        if extracted:
            sugar_val = extracted

    if sugar_val == "미정":
        carbo_index = -1
        for i, (box, original_text, fixed_text) in enumerate(valid_results):
            if any(k in fixed_text for k in carbo_keywords) or '12' in fixed_text:
                carbo_index = i
                break
        
        if carbo_index != -1 and carbo_index + 1 < len(valid_results):
            extracted = extract_sugar_with_spatial_filter(valid_results, carbo_index + 1)
            if extracted:
                sugar_val = extracted
        
    if sugar_val == "미정":
        sugar_val = "미정 (재촬영 필요)"

    for box, original_text, fixed_text in valid_results:
        if any(k in fixed_text for k in ['비타민', '타민', '비타']):
            if any(k in fixed_text for k in ['A', 'a', 'D', 'd']):
                match_num = re.search(r'\d+', fixed_text)
                vit_num = match_num.group() if match_num else ""
                vitamins.append(f"{original_text} ({vit_num}mg/μg)" if vit_num else original_text)

        if '카페인' in fixed_text or '카페' in fixed_text:
            match_caf = re.search(r'\d+', fixed_text)
            if match_caf:
                caffeine_val = f"{match_caf.group()}mg"

    print(f"🍬 1. 당류 함량 : {sugar_val}")
    print(f"💊 2. 비타민 A/D: {', '.join(vitamins) if vitamins else '없음 또는 미검출'}")
    print(f"☕ 3. 카페인    : {caffeine_val}")
    print("=======================================================")
    print(f"⚡ [분석 완료] 총 소요 시간: {time.time() - start_time:.2f}초\n")

    return {
        "sugar": sugar_val,
        "vitamin": ', '.join(vitamins) if vitamins else "없음",
        "caffeine": caffeine_val
    }


async def run_camera_and_send(front_bot=None):
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("카메라를 열 수 없습니다.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    print("\n=== 📸 camera_track.py 프론트 연동 가이드 버전 가동 ===")
    print("- 초록색 사각형 가이드 박스 안에 성분표를 맞춰주세요.")
    print("- Spacebar: 가이드 박스 내부 영역만 캡처 및 프론트 전송")
    print("- Esc: 프로그램 안전 종료")
    print("=======================================")

    while True:
        ret, frame = cap.read()
        if not ret: break
        
        height, width, _ = frame.shape
        
        box_w, box_h = 350, 350
        x = int((width - box_w) / 2)
        y = int((height - box_h) / 2)
        
        display_frame = frame.copy()
        
        cv2.rectangle(display_frame, (x, y), (x + box_w, y + box_h), (0, 255, 0), 2)
        cv2.putText(display_frame, "ALIGN NUTRITION TABLE HERE", (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        
        cv2.imshow('Webcam', display_frame)
        key = cv2.waitKey(1) & 0xFF

        if key == 32 or key == ord(' '):
            filename = "photo.jpg"
            
            roi_crop = frame[y:y+box_h, x:x+box_w]
            cv2.imwrite(filename, roi_crop)
            
            result_data = process_ocr_analysis(filename, reader)
            
            if result_data and front_bot is not None:
                sugar_value = result_data["sugar"]
                print(f"📡 [결합] 프론트 봇에게 당류 수치({sugar_value}) 전송 중...")
                await front_bot.send_message(sugar_value) 
                print("✅ [결합] 전송 완료!")
            else:
                print("💡 [알림] 프론트 봇 연동 대기 중 (터미널 출력 완료)")
            
        elif key == 27:
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_camera_and_send(front_bot=None))