import os
import sys
import time
import re
import cv2
import numpy as np

# macOS Native Vision Framework
import Vision
from Cocoa import NSURL


# =======================================================
# 🎨 1. 음료 색상 분석
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
# 🔬 2. 영양성분표 맞춤형 전처리 (선명도 최적화: 5/6 뭉개짐 방지)
# =======================================================
def preprocess_nutrition_label(image_path):
    img = cv2.imread(image_path)
    if img is None:
        return image_path

    # 1. 2.5배 확대 (자간 및 획 뭉개짐 방지)
    resized = cv2.resize(img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)

    # 2. 적절한 히스토그램 평활화 (대비 강조)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # 3. 샤프닝 필터 (글자 테두리를 또렷하게 보정)
    sharpen_kernel = np.array([[0, -1, 0],
                               [-1, 5, -1],
                               [0, -1, 0]])
    sharpened = cv2.filter2D(enhanced, -1, sharpen_kernel)

    # 4. 강한 이진화 대신 디노이즈 처리된 회색조 이미지 생성
    denoised = cv2.fastNlMeansDenoising(sharpened, None, h=10, templateWindowSize=7, searchWindowSize=21)

    prep_path = os.path.abspath("temp_vision_prep.jpg")
    cv2.imwrite(prep_path, denoised)
    return prep_path


# =======================================================
# 🛠️ 3. 문자열 및 단위 오인식 후처리 정규화
# =======================================================
def normalize_ocr_text(text):
    t = text

    # 1. 단위 오인식 교정
    t = re.sub(r'rng|rnq|mq', 'mg', t, flags=re.IGNORECASE)
    t = re.sub(r'mI|ML', 'mL', t)
    t = re.sub(r'(\d+)\s*(?:q|a|C)(?![a-zA-Z])', r'\1g', t)
    t = re.sub(r'(\d+)\s*Sg', r'\1 5g', t)

    # 2. 당류 오인식 키워드 보정
    misread_sugar = ['당루', '당로', '당뮤', '당료', '담류', '탕류', '당 류', '당규']
    for k in misread_sugar:
        t = t.replace(k, '당류')

    # 3. 붙어 나온 문자와 숫자 띄어쓰기 분리 ('당류15g나트륨' -> '당류 15g 나트륨')
    t = re.sub(r'([가-힣]+)(\d+)', r'\1 \2', t)
    t = re.sub(r'(\d+)(g|mg|mL|ml)([가-힣]+)', r'\1\2 \3', t)

    return t


# =======================================================
# 🍏 4. Apple Vision OCR + Y축 라인 빌더 (Line Builder)
# =======================================================
def run_vision_ocr_line_by_line(image_path):
    prep_path = preprocess_nutrition_label(image_path)
    input_url = NSURL.fileURLWithPath_(os.path.abspath(prep_path))
    request_handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(input_url, None)

    raw_blocks = []

    def completion_handler(request, error):
        if error:
            return
        observations = request.results()
        for observation in observations:
            top_candidate = observation.topCandidates_(1)[0]
            text = normalize_ocr_text(top_candidate.string())
            bbox = observation.boundingBox()
            y_center = bbox.origin.y + (bbox.size.height / 2.0)
            x_min = bbox.origin.x

            raw_blocks.append({
                'text': text,
                'x': x_min,
                'y': y_center
            })

    request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(completion_handler)
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["ko-KR", "en-US"])
    request.setUsesLanguageCorrection_(False)

    request_handler.performRequests_error_([request], None)

    # Y축 내림차순 정렬
    raw_blocks.sort(key=lambda b: b['y'], reverse=True)

    lines = []
    y_threshold = 0.015  # 동일 행 판정 오차범위 (1.5%)

    for block in raw_blocks:
        matched_line = None
        for line in lines:
            if abs(line['y'] - block['y']) < y_threshold:
                matched_line = line
                break

        if matched_line:
            matched_line['blocks'].append(block)
            matched_line['y'] = sum(b['y'] for b in matched_line['blocks']) / len(matched_line['blocks'])
        else:
            lines.append({
                'y': block['y'],
                'blocks': [block]
            })

    reconstructed_lines = []
    for line in lines:
        line['blocks'].sort(key=lambda b: b['x'])
        full_line_text = " ".join([b['text'] for b in line['blocks']])
        clean_text = full_line_text.replace(" ", "")
        reconstructed_lines.append({
            'y': line['y'],
            'text': full_line_text,
            'clean': clean_text
        })

    return reconstructed_lines


# =======================================================
# 🎯 5. 수학적 자동 교정이 포함된 영양성분 파서
# =======================================================
def parse_nutrition_label(lines):
    sugar_val = None
    volume_val = None
    carbo_y = None
    protein_y = None

    # ---------------------------------------------------
    # 1. 총 내용량(Volume) 범용 매칭
    # ---------------------------------------------------
    for line in lines:
        clean = line['clean']

        if volume_val is None:
            m_vol = re.search(r'(?:총내용량|내용량|용량)[^\d]*(\d+\.?\d*)\s*(?:mL|ml|g|L|l)?', clean, re.IGNORECASE)
            if not m_vol:
                m_vol = re.search(r'\(\s*(\d+\.?\d*)\s*(?:mL|ml|g)\s*\)', clean, re.IGNORECASE)
            if not m_vol:
                m_vol = re.search(r'(\d+\.?\d*)\s*(?:mL|ml)', clean, re.IGNORECASE)

            if m_vol:
                raw_v = float(m_vol.group(1))
                if 'L' in clean or 'l' in clean:
                    if raw_v < 10:
                        raw_v = raw_v * 1000

                v_num = int(raw_v)
                if 10 <= v_num <= 3000:
                    volume_val = f"{v_num}ml (또는 g)"

    # ---------------------------------------------------
    # 2. 당류(Sugar) 추출 및 🧮 수학적 영양기준치(%) 교정
    # ---------------------------------------------------
    for line in lines:
        clean = line['clean']
        text = line['text']

        if '탄수화물' in clean and carbo_y is None:
            carbo_y = line['y']
        if ('단백질' in clean or '지방' in clean) and protein_y is None:
            protein_y = line['y']

        if '당류' in clean and sugar_val is None:
            # 함량(g)과 영양성분 기준치 비율(%)을 동시 추출
            # 예: '당류 16g 15%' 또는 '당류 15g 15%' 패턴 매칭
            m_sugar_pct = re.search(r'당류[^\d]*(\d+\.?\d*)\s*g?\s*(\d+)\s*%', clean, re.IGNORECASE)

            if m_sugar_pct:
                raw_g = float(m_sugar_pct.group(1))
                raw_pct = float(m_sugar_pct.group(2))

                # 식약처 당류 1일 기준치: 100g (1g = 1%)
                # 불일치 발생 시 % 비율로 수학적 자동 교정 (ex: 16g 15% -> 15g 교정)
                if abs(raw_g - raw_pct) >= 1.0:
                    corrected_g = raw_pct  # %수치가 기준치 계산상 원본 함량 g과 동일
                    sugar_val = f"{int(corrected_g) if corrected_g.is_integer() else corrected_g}g (수학적 %교정)"
                else:
                    sugar_val = f"{int(raw_g) if raw_g.is_integer() else raw_g}g"
            else:
                # % 단서가 없는 일반 수치 파싱
                m_sugar = re.search(r'당류[^\d]*(\d+\.?\d*)', clean, re.IGNORECASE)
                if m_sugar and 'mg' not in clean.lower():
                    val_str = m_sugar.group(1)
                    val = float(val_str)

                    if val > 100 and val_str.endswith('9'):
                        val = float(val_str[:-1])

                    if val <= 100:
                        sugar_val = f"{int(val) if val.is_integer() else val}g"

    # ---------------------------------------------------
    # 3. [공간 맥락 추론] 키워드 훼손 시 탄수화물 하단 추적
    # ---------------------------------------------------
    if sugar_val is None and carbo_y is not None:
        for line in lines:
            is_between = (line['y'] < carbo_y) and (protein_y is None or line['y'] > protein_y)
            if is_between:
                m_sub = re.search(r'(\d+\.?\d*)\s*g?', line['text'], re.IGNORECASE)
                if m_sub and 'mg' not in line['clean'].lower() and 'kcal' not in line['clean'].lower():
                    val = float(m_sub.group(1))
                    if val <= 100:
                        sugar_val = f"{int(val) if val.is_integer() else val}g (위치 추론)"
                        break

    # ---------------------------------------------------
    # 4. 기타 성분 (비타민, 카페인)
    # ---------------------------------------------------
    vitamins = []
    caffeine_val = "0mg 또는 없음"

    for line in lines:
        clean = line['clean']
        text = line['text']

        if '비타민' in clean and len(clean) < 25:
            m_vit = re.search(r'(비타민\s*[A-Za-z0-9]+\s*\d*\s*(?:mg|μg|g|%)?)', text, re.IGNORECASE)
            if m_vit:
                vitamins.append(m_vit.group(1).strip())

        if '카페인' in clean:
            m_caf = re.search(r'(\d+)\s*mg', clean, re.IGNORECASE)
            if m_caf:
                caffeine_val = f"{m_caf.group(1)}mg"

    return {
        'volume': volume_val if volume_val else "미정 (인식 실패)",
        'sugar': sugar_val if sugar_val else "미정 (인식 실패)",
        'vitamins': list(set(vitamins)),
        'caffeine': caffeine_val
    }


# =======================================================
# 🧪 6. 통합 실행 및 결과 출력
# =======================================================
def process_ocr_analysis(image_path):
    start_time = time.time()
    print("\n---------------------------------------------------")
    print("⚡ [범용 영양성분표 OCR] 고정밀 파싱 프로세스")
    print("---------------------------------------------------")

    img = cv2.imread(image_path)
    if img is None:
        print("❌ [에러] 이미지를 열 수 없습니다.")
        return

    color_result = analyze_liquid_color(img)
    lines = run_vision_ocr_line_by_line(image_path)

    print("\n[🔍 OCR 인식 라인 디버깅]")
    for idx, l in enumerate(lines):
        print(f"  Line {idx+1}: {l['text']}")
    print("---------------------------------------------------\n")

    parsed = parse_nutrition_label(lines)

    print("================== [최종 분석 결과] ==================")
    print(f"🎨 음료 색상 판정       : {color_result}")
    print(f"📦 영양성분표 인식 용량 : {parsed['volume']}")
    print(f"🍬 1. 당류 함량          : {parsed['sugar']}")
    print(f"💊 2. 비타민류           : {', '.join(parsed['vitamins']) if parsed['vitamins'] else '없음 또는 미검출'}")
    print(f"☕ 3. 카페인             : {parsed['caffeine']}")
    print("=======================================================")
    print(f"⚡ [분석 완료] 소요 시간: {time.time() - start_time:.2f}초\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
        if os.path.exists(target):
            process_ocr_analysis(target)