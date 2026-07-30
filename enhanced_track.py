import os
import ssl
import cv2
import easyocr
import re
import time
import numpy as np
import subprocess

# 🔥 [맥북 Segfault 100% 차단] 백그라운드 스레드 및 메모리 경합 원천 차단
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
cv2.setNumThreads(0)
cv2.ocl.setUseOpenCL(False)

# [안전장치] 맥북 환경 SSL 다운로드 차단 현상 방지
ssl._create_default_https_context = ssl._create_unverified_context

if not os.path.exists('test_samples'):
    os.makedirs('test_samples')

# 오늘 누적 총 음수량 (ml)
water_today = 0


# =======================================================
# 🎨 1. 음료 색상 분석 및 무색/투명 예외 처리 (Color Thresholding)
# =======================================================
def analyze_liquid_color(image_roi):
    if image_roi is None or image_roi.size == 0:
        return "알 수 없음"

    hsv = cv2.cvtColor(image_roi, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    valid_color_mask = (s > 40) & (v > 30) & (v < 220)
    valid_pixel_count = np.sum(valid_color_mask)
    total_pixels = image_roi.shape[0] * image_roi.shape[1]

    valid_ratio = valid_pixel_count / total_pixels
    if valid_ratio < 0.05:
        return "💧 무색/투명 (물, 탄산수 등)"

    valid_hues = h[valid_color_mask]
    avg_hue = np.mean(valid_hues)

    if (0 <= avg_hue < 10) or (170 <= avg_hue <= 180):
        return "🔴 빨강 계열 (체리, 석류 등)"
    elif 10 <= avg_hue < 25:
        return "🟠 주황 계열 (오렌지, 자몽 등)"
    elif 25 <= avg_hue < 35:
        return "🟡 노랑 계열 (레몬, 망고 등)"
    elif 35 <= avg_hue < 85:
        return "🟢 초록 계열 (청포도, 녹차 등)"
    elif 85 <= avg_hue < 130:
        return "🔵 파랑/보라 계열 (블루베리, 이온음료 등)"
    elif 130 <= avg_hue < 170:
        return "🟤 갈색/보라 계열 (콜라, 커피 등)"

    return "🍹 기타 유색 음료"


# =======================================================
# 📏 2. 공간 좌표 및 정규식 기반 수치 보정 로직
# =======================================================
def parse_number_and_unit_by_geometry(valid_results, target_idx):
    target_box, _, target_text = valid_results[target_idx]
    
    if re.search(r'(?:0|o|O)\s*(?:g|9|%|그램)', target_text):
        return "0g"

    box_x_min = target_box[0][0]
    box_x_max = target_box[1][0]
    box_width = box_x_max - box_x_min
    
    box_height = target_box[2][1] - target_box[0][1]
    target_y_center = (target_box[0][1] + target_box[2][1]) / 2

    digits = re.findall(r'\d+', target_text)
    if not digits:
        return None
        
    full_num_str = digits[0]
    num_val = int(full_num_str)
    
    # 4130g -> 41g 컷오프 보정
    if num_val >= 100:
        if len(full_num_str) >= 3:
            real_num = full_num_str[:2]
            return f"{int(real_num)}g"
            
        if any(c in target_text.lower() for c in ['0', 'o', '%', 'g']):
            return "0g"
        return None

    if len(full_num_str) >= 2 and full_num_str.endswith('9') and 'g' not in target_text.lower():
        pure_num = full_num_str[:-1]
        return f"{pure_num}g"
        
    return f"{full_num_str}g"


def extract_total_volume(valid_results):
    volume_keywords = ['총내용량', '내용량', '총내']
    for i, (box, original_text, fixed_text) in enumerate(valid_results):
        if any(k in fixed_text for k in volume_keywords):
            match = re.search(r'(\d+)\s*(?:m?[lLg그])', fixed_text)
            if match:
                return int(match.group(1))
                
            search_range = range(i + 1, min(i + 4, len(valid_results)))
            for idx in search_range:
                next_text = valid_results[idx][2]
                match_next = re.search(r'\d+', next_text)
                if match_next:
                    return int(match_next.group())
    return 350


def extract_pure_sugar(valid_results, sugar_idx, carbo_idx):
    sugar_box, _, sugar_fixed = valid_results[sugar_idx]
    sugar_x_right = sugar_box[1][0]
    sugar_y_center = (sugar_box[0][1] + sugar_box[2][1]) / 2
    sugar_height = sugar_box[2][1] - sugar_box[0][1]
    
    carbo_y_bottom = -1
    if carbo_idx != -1:
        carbo_y_bottom = valid_results[carbo_idx][0][2][1]

    for i, (box, original_text, fixed_text) in enumerate(valid_results):
        if i == sugar_idx or i == carbo_idx:
            continue
            
        current_x_left = box[0][0]
        current_y_top = box[0][1]
        current_y_center = (box[0][1] + box[2][1]) / 2
        
        if carbo_y_bottom != -1 and current_y_top < carbo_y_bottom - 5:
            continue
            
        if abs(sugar_y_center - current_y_center) <= sugar_height * 0.7:
            x_distance = current_x_left - sugar_x_right
            if -15 <= x_distance <= sugar_height * 5.0:
                result_sugar = parse_number_and_unit_by_geometry(valid_results, i)
                if result_sugar:
                    return result_sugar

    inline_result = parse_number_and_unit_by_geometry(valid_results, sugar_idx)
    if inline_result and inline_result != "g":
        return inline_result

    if sugar_idx + 1 < len(valid_results):
        next_result = parse_number_and_unit_by_geometry(valid_results, sugar_idx + 1)
        if next_result:
            return next_result

    return None


# =======================================================
# 🧪 3. 고정밀 OCR 분석 프로세스 (Worker에서 직접 호출)
# =======================================================
def process_ocr_analysis(image_path):
    global water_today
    start_time = time.time()
    print("\n---------------------------------------------------")
    print("⏳ [AI 통합 분석] 고정밀 엔진 로딩 및 이미지 분석 가동 중...")
    print("---------------------------------------------------")
    
    img = cv2.imread(image_path)
    if img is None:
        print("❌ [에러] 이미지를 열 수 없습니다.")
        return None

    color_result = analyze_liquid_color(img)

    resized_img = cv2.resize(img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(resized_img, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.CLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(gray)
    
    temp_enhanced_path = os.path.abspath("temp_enhanced.jpg")
    cv2.imwrite(temp_enhanced_path, enhanced)
    time.sleep(0.15) # 파일 입출력 시점 보장

    # 🔥 독립적인 EasyOCR 엔진 호출
    local_reader = easyocr.Reader(['ko', 'en'], gpu=False)

    results = local_reader.readtext(
        temp_enhanced_path, paragraph=False, decoder='beamsearch', beamWidth=7,
        allowlist='당류탄수화물단백질지방나트륨비타민카페인영양성분정보기준치총내용량0123456789g%mlLADad ,_`탕뉴량규유슈'
    )

    valid_results = []
    for res in results:
        box = res[0]
        original_text = res[1]
        fixed_text = res[1].replace(" ", "")
        valid_results.append((box, original_text, fixed_text))

    detected_volume = extract_total_volume(valid_results)
    water_today += detected_volume

    sugar_val = "미정"
    vitamins = []
    caffeine_val = "0mg 또는 없음"

    sugar_keywords = ['당류', '당뉴', '탕류', '당규', '량류', '당유', '탄수화물당']
    carbo_keywords = ['탄수화물', '탄수', '탄슈', '탄수화']

    sugar_index = -1
    carbo_index = -1
    for i, (box, original_text, fixed_text) in enumerate(valid_results):
        if sugar_index == -1 and any(k in fixed_text for k in sugar_keywords):
            sugar_index = i
        if carbo_index == -1 and any(k in fixed_text for k in carbo_keywords):
            carbo_index = i

    if sugar_index != -1:
        extracted = extract_pure_sugar(valid_results, sugar_index, carbo_index)
        if extracted:
            sugar_val = extracted

    if sugar_val == "미정" and carbo_index != -1 and carbo_index + 1 < len(valid_results):
        extracted = extract_pure_sugar(valid_results, carbo_index + 1, carbo_index)
        if extracted:
            sugar_val = extracted
        
    if sugar_val == "미정":
        sugar_val = "미정 (재촬영 필요)"

    for box, original_text, fixed_text in valid_results:
        if any(k in fixed_text for k in ['비타민', '타민', '비타']):
            if any(k in fixed_text for k in ['A', 'a', 'D', 'd', 'C', 'c']):
                match_num = re.search(r'\d+', fixed_text)
                vit_num = match_num.group() if match_num else ""
                vitamins.append(f"{original_text} ({vit_num}mg/μg)" if vit_num else original_text)

        if '카페인' in fixed_text or '카페' in fixed_text:
            match_caf = re.search(r'\d+', fixed_text)
            if match_caf:
                caffeine_val = f"{match_caf.group()}mg"

    print("================== [최종 분석 결과] ==================")
    print(f"🎨 음료 색상 판정       : {color_result}")
    print(f"📦 영양성분표 인식 용량 : {detected_volume}ml (또는 g)")
    print(f"📊 오늘 누적 총 음수량  : {water_today}ml")
    print(f"🍬 1. 당류 함량          : {sugar_val}")
    print(f"💊 2. 비타민류           : {', '.join(vitamins) if vitamins else '없음 또는 미검출'}")
    print(f"☕ 3. 카페인             : {caffeine_val}")
    print("=======================================================")
    print(f"⚡ [분석 완료] 총 소요 시간: {time.time() - start_time:.2f}초\n")

    return {
        "color": color_result,
        "detected_volume": f"{detected_volume}ml",
        "sugar": sugar_val,
        "vitamin": ', '.join(vitamins) if vitamins else "없음",
        "caffeine": caffeine_val
    }


# =======================================================
# 🎥 4. 웹캠 제어 및 실행
# =======================================================
async def run_camera_and_send(front_bot=None):
    cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        cap = cv2.VideoCapture(1, cv2.CAP_AVFOUNDATION)

    if not cap.isOpened():
        print("❌ [에러] 맥북 카메라를 열 수 없습니다.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    print("\n=== 📸 통합 영양성분 & 색상 스캐너 가동 ===")
    print("- Spacebar: 가이드 박스 영역 크롭 캡처 및 분석 가동")
    print("- Esc: 프로그램 안전 종료")
    print("=======================================")

    while True:
        ret, frame = cap.read()
        if not ret: 
            break
        
        height, width, _ = frame.shape
        box_w, box_h = 350, 350
        x = int((width - box_w) / 2)
        y = int((height - box_h) / 2)
        
        # 🟢 [수정완료] roi_crop 변수를 루프 내부에서 정상 할당
        roi_crop = frame[y:y+box_h, x:x+box_w]

        display_frame = frame.copy()
        cv2.rectangle(display_frame, (x, y), (x + box_w, y + box_h), (0, 255, 0), 2)
        cv2.putText(display_frame, "ALIGN NUTRITION TABLE HERE", (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        
        cv2.imshow('Webcam', display_frame)
        key = cv2.waitKey(1) & 0xFF

        if key == 32: # Spacebar
            filename = "photo.jpg"
            cv2.imwrite(filename, roi_crop)
            print(f"\n📸 [캡처 완료] 영역 이미지 저장: {filename}")
    
            print("⏳ [AI 독립 프로세스] ocr_worker.py 분석 가동...")
            
            # 독립 프로세스로 worker 호출
            result = subprocess.run(["python3", "ocr_worker.py", filename], capture_output=True, text=True)
            print(result.stdout)
            
            if result.stderr:
                print(f"⚠️ [Worker 로그]: {result.stderr}")
            
            if front_bot is not None:
                await front_bot.send_message("영양성분 분석 처리가 완료되었습니다.")
            
        elif key == 27: # ESC
            print("\n👋 프로그램을 종료합니다.")
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_camera_and_send(front_bot=None))