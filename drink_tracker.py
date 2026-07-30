import os
import ssl
import cv2
import easyocr
import re
import time
import numpy as np

# [안전장치] 맥북 환경 SSL 및 멀티스레드 충돌 방지
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "1"
ssl._create_default_https_context = ssl._create_unverified_context

if not os.path.exists('test_samples'):
    os.makedirs('test_samples')

print("⚙️ [시스템] EasyOCR 모델을 로딩 중입니다...")
# gpu=False 강제 지정으로 맥북 GPU 메모리 충돌 차단
reader = easyocr.Reader(['ko', 'en'], gpu=False)
print("✅ [시스템] AI 엔진 로드 완료!")

water_today = 0


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


def parse_number_and_unit_by_geometry(valid_results, target_idx):
    target_box, _, target_text = valid_results[target_idx]
    
    box_x_min = target_box[0][0]
    box_x_max = target_box[1][0]
    box_width = box_x_max - box_x_min
    
    adjacent_unit = None
    for i, (other_box, _, other_text) in enumerate(valid_results):
        if i == target_idx:
            continue
            
        other_x_min = other_box[0][0]
        other_y_center = (other_box[0][1] + other_box[2][1]) / 2
        target_y_center = (target_box[0][1] + target_box[2][1]) / 2
        
        if abs(target_y_center - other_y_center) < (target_box[2][1] - target_box[0][1]) * 0.7:
            x_gap = other_x_min - box_x_max
            if 0 <= x_gap <= box_width * 1.5:
                if 'g' in other_text.lower() or '9' in other_text or '그램' in other_text:
                    adjacent_unit = "g"
                    break

    digits = re.findall(r'\d+', target_text)
    if not digits:
        return None
        
    full_num_str = digits[0]
    if len(full_num_str) >= 2 and full_num_str.endswith('9') and 'g' not in target_text.lower():
        pure_num = full_num_str[:-1]
        return f"{pure_num}g"
        
    if adjacent_unit:
        return f"{full_num_str}g"
        
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


def process_ocr_analysis(image_path, ocr_reader):
    global water_today
    start_time = time.time()
    print("\n---------------------------------------------------")
    print("⏳ [AI 통합 분석] 성분 판독, 색상 분석 및 자간 보정 가동...")
    print("---------------------------------------------------")
    
    img = cv2.imread(image_path)
    if img is None:
        print("❌ [에러] 이미지를 불러올 수 없습니다.")
        return None

    color_result = analyze_liquid_color(img)

    # 🎯 [핵심] 메모리 Crash 방지: OpenCV 메모리를 넘기지 않고 파일 경로를 직접 readtext에 전달
    results = ocr_reader.readtext(
        image_path, 
        paragraph=False, 
        decoder='beamsearch', 
        beamWidth=7,
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

    print("================== [최종 결합 분석 결과] ==================")
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
        "water_today": f"{water_today}ml",
        "sugar": sugar_val,
        "vitamin": ', '.join(vitamins) if vitamins else "없음",
        "caffeine": caffeine_val
    }


async def run_camera_and_send(front_bot=None):
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        cap = cv2.VideoCapture(1)

    if not cap.isOpened():
        print("❌ [에러] 맥북 카메라인식을 실패했습니다.")
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
        
        display_frame = frame.copy()
        cv2.rectangle(display_frame, (x, y), (x + box_w, y + box_h), (0, 255, 0), 2)
        
        cv2.imshow('Webcam', display_frame)
        key = cv2.waitKey(1) & 0xFF

        if key == 32 or key == ord(' '):
            filename = "photo.jpg"
            roi_crop = frame[y:y+box_h, x:x+box_w]
            # 파일로 안전 저장 후 경로 매개변수 전달
            cv2.imwrite(filename, roi_crop)
            
            result_data = process_ocr_analysis(filename, reader)
            
            if result_data and front_bot is not None:
                await front_bot.send_message(str(result_data)) 
            
        elif key == 27:
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_camera_and_send(front_bot=None))