import os
import ssl
import cv2
import easyocr
import re
import time
import numpy as np
import threading  # 멀티스레딩
import asyncio
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# 맥북 SSL 다운로드 차단 현상 방지
ssl._create_default_https_context = ssl._create_unverified_context

if not os.path.exists('test_samples'):
    os.makedirs('test_samples')

print("⚙️ [시스템] EasyOCR 모델을 로딩 중입니다...")
reader = easyocr.Reader(['ko', 'en'], gpu=False)
print("✅ [시스템] AI 엔진 로드 완료!")

# =======================================================
# 1. 실시간 누적 데이터 구조 세팅
# =======================================================
TOKEN = "8746270836:AAG_wac0YY-wLmNHTG2LpDO3uT3vktJva_Q"
WEB_URL = "http://127.0.0.1:5000"

target_water = 1200          # 하루 목표 음수량 ml
water_today = 650            # 오늘 누적 음수량 ml

sugar_limit = 50             # 하루 당류 기준 g
sugar_today = 0              

caffeine_limit = 300         # 하루 카페인 기준 mg
caffeine_today = 0           

vitamin_goal = 100           # 하루 비타민 목표 mg
vitamin_today = 0            

last_drink_minutes = 130     
user_chat_id = None          
telegram_app = None          
main_loop = None             

recent_logs = []

# =======================================================
# 2. 딥러닝 OCR 성분 분석 및 실시간 데이터 연동
# =======================================================
def process_ocr_analysis(image_path, ocr_reader):
    global sugar_today, caffeine_today, vitamin_today, recent_logs, main_loop
    start_time = time.time()
    print("\n---------------------------------------------------")
    print("⏳ [AI 가동] 3대 핵심 성분(당류/비타민/카페인) 분석 시작...")
    print("---------------------------------------------------")
    
    img = cv2.imread(image_path)
    if img is None:
        print("❌ [에러] 분석할 이미지를 찾을 수 없습니다.")
        return None

    try:
        results = ocr_reader.readtext(
            img, paragraph=False, decoder='beamsearch', beamWidth=7,
            allowlist='당류탄수화물단백질지방나트륨비타민카페인영양성분정보기준치0123456789g%mlL ,_`'
        )
    except Exception as e:
        print(f"❌ [OCR 자체 에러] {e}")
        return None

    print("\n================== [1차 AI 원본 인식 목록] ==================")
    valid_results = []
    for res in results:
        raw_text = res[1].replace(" ", "")
        print(f"인식된 글자: {res[1]}")
        valid_results.append((raw_text, res[1]))
    print("===========================================================\n")

    def extract_number(text_string):
        match = re.search(r'\d{1,5}', text_string)
        return match.group() if match else ""

    sugar_val = "0"
    vitamins = []
    extracted_vitamin_total = 0
    caffeine_val = "0"

    for fixed_text, original_text in valid_results:
        if '당류' in fixed_text or '당' in fixed_text:
            num = extract_number(fixed_text)
            if num: 
                if len(num) >= 2 and num.endswith('9'):
                    corrected_num = num[:-1]
                    sugar_val = corrected_num if corrected_num else "0"
                else:
                    sugar_val = num
            
        elif '비타민' in fixed_text or '타민' in fixed_text:
            vit_num = extract_number(fixed_text)
            if vit_num:
                vitamins.append(f"{original_text} ({vit_num}mg)")
                try:
                    extracted_vitamin_total += int(vit_num)
                except:
                    pass
            else:
                vitamins.append(original_text)

        elif '카페인' in fixed_text:
            caf_num = extract_number(fixed_text)
            if caf_num: 
                caffeine_val = caf_num

    extracted_sugar = int(sugar_val) if sugar_val.isdigit() else 0
    extracted_caffeine = int(caffeine_val) if caffeine_val.isdigit() else 0

    # 누적 데이터 업데이트
    sugar_today += extracted_sugar
    caffeine_today += extracted_caffeine
    vitamin_today += extracted_vitamin_total

    current_time_str = time.strftime("%H:%M", time.localtime())
    recent_logs.append({
        "time": current_time_str,
        "drink": "인식된 음료",
        "amount": 350,
        "sugar": extracted_sugar,
        "caffeine": extracted_caffeine,
        "vitamin": extracted_vitamin_total,
        "source": "OCR 실시간 인식"
    })

    print("\n================== [최종 성분 리포트] ==================")
    print(f"🍬 1. 당류 함량 : {extracted_sugar}g (누적: {sugar_today}g)")
    print(f"🍋 2. 비타민류  : {', '.join(vitamins) if vitamins else '없음 또는 미검출'} (+{extracted_vitamin_total}mg, 누적: {vitamin_today}mg)")
    print(f"☕ 3. 카페인    : {extracted_caffeine}mg (누적: {caffeine_today}mg)")
    print("=======================================================")
    print(f"⚡ [분석 완료] 연산 처리 소요 시간: {time.time() - start_time:.2f}초\n")
    
    if telegram_app and user_chat_id and main_loop:
        alert_msg = (
            "🎯 [OCR 성분 분석 완료]\n\n"
            f"🥤 음료 인식에 성공하여 오늘 데이터에 반영되었습니다!\n"
            f"🍬 당류 +{extracted_sugar}g 추가 (총 {sugar_today}g)\n"
            f"☕ 카페인 +{extracted_caffeine}mg 추가 (총 {caffeine_today}mg)\n"
            f"🍋 비타민 +{extracted_vitamin_total}mg 추가 (총 {vitamin_today}mg)"
        )
        asyncio.run_coroutine_threadsafe(
            telegram_app.bot.send_message(chat_id=user_chat_id, text=alert_msg),
            main_loop
        )

    return {"sugar": extracted_sugar, "vitamins": vitamins, "vitamin_total": extracted_vitamin_total, "caffeine": extracted_caffeine}

# =======================================================
# 3. OpenCV 웹캠 가이드라인 (메인 스레드 전용)
# =======================================================
def run_camera():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ [에러] 카메라를 열 수 없습니다. Mac 시스템 설정에서 카메라 권한을 확인해주세요.")
        return

    print("\n=== 📸 웹캠 엔진 시동 완료 ===")
    print("- 웹캠 창이 뜨면 가이드 박스에 성분표를 맞추고 [Spacebar]를 누르세요.")
    print("- 종료하려면 [Esc]를 누르세요.\n")
    
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
        
        cv2.imshow('Webcam View (Deep Learning Part)', display_frame)
        key = cv2.waitKey(1) & 0xFF

        if key == 32 or key == ord(' '):  # 스페이스바
            filename = "photo.jpg"
            roi_crop = frame[y:y+box_h, x:x+box_w]
            cv2.imwrite(filename, roi_crop)
            process_ocr_analysis(filename, reader)
            
        elif key == 27:  # ESC 키
            break

    cap.release()
    cv2.destroyAllWindows()

# =======================================================
# 4. 수분/영양소 계산 유틸리티
# =======================================================
def calculate_rate(value, limit):
    if limit == 0: return 0
    return int(value / limit * 100)

def get_water_rate(): return calculate_rate(water_today, target_water)
def get_sugar_rate(): return calculate_rate(sugar_today, sugar_limit)
def get_caffeine_rate(): return calculate_rate(caffeine_today, caffeine_limit)
def get_vitamin_rate(): return calculate_rate(vitamin_today, vitamin_goal)

def get_water_emoji(rate):
    if rate >= 100: return "🎉"
    elif rate >= 71: return "🟢"
    elif rate >= 51: return "🟡"
    else: return "🔴"

def get_ingredient_emoji(rate):
    if rate >= 90: return "🔴"
    elif rate >= 70: return "🟡"
    else: return "🟢"

def get_vitamin_emoji(rate):
    if rate >= 70: return "🟢"
    elif rate >= 30: return "🟡"
    else: return "🔴"

# =======================================================
# 5. 텔레그램 메세지 구성 함수군
# =======================================================
def make_water_message():
    rate = get_water_rate()
    if rate <= 30: status, comment = "🔴 매우 부족", "오늘 수분 섭취량이 많이 부족해요. 지금은 물을 우선적으로 마시는 것이 좋습니다."
    elif rate <= 50: status, comment = "🔴 부족", "현재 수분 섭취량이 부족해요. 다음 음료는 물을 추천합니다."
    elif rate <= 70: status, comment = "🟡 주의", "목표량에 가까워지고 있어요. 조금만 더 마시면 안정적인 상태에 도달할 수 있습니다."
    elif rate <= 100: status, comment = "🟢 양호", "좋아요! 오늘 수분 섭취가 비교적 안정적으로 이루어지고 있습니다."
    else: status, comment = "🎉 목표 달성", "오늘 목표 음수량을 달성했어요. 과하게 마시기보다는 일정한 간격을 유지해보세요."

    return f"💧 오늘 음수량\n\n현재 음수량: {water_today}ml\n목표 음수량: {target_water}ml\n달성률: {rate}%\n상태: {status}\n\n{comment}"

def make_sugar_message():
    rate = get_sugar_rate()
    if rate >= 90: status, comment = "🔴 위험", "당류 섭취량이 높은 편이에요. 다음 음료는 물이나 무가당 음료를 추천합니다."
    elif rate >= 70: status, comment = "🟡 주의", "당류 섭취량이 기준에 가까워지고 있어요. 단 음료 섭취에 주의해주세요."
    else: status, comment = "🟢 안정", "현재 당류 섭취 상태는 안정적인 편입니다."

    return f"🍬 오늘 당류\n\n오늘 섭취 당류: {sugar_today}g\n기준 당류: {sugar_limit}g\n기준 대비: {rate}%\n상태: {status}\n\n{comment}"

def make_caffeine_message():
    rate = get_caffeine_rate()
    if rate >= 90: status, comment = "🔴 위험", "카페인 섭취량이 높은 편이에요. 오늘은 커피나 에너지드링크 섭취를 줄이는 것이 좋습니다."
    elif rate >= 70: status, comment = "🟡 주의", "카페인 섭취량이 기준에 가까워지고 있어요. 다음 음료는 카페인이 없는 음료를 추천합니다."
    else: status, comment = "🟢 안정", "현재 카페인 섭취 상태는 안정적인 편입니다."

    return f"☕ 오늘 카페인\n\n오늘 섭취 카페인: {caffeine_today}mg\n기준 카페인: {caffeine_limit}mg\n기준 대비: {rate}%\n상태: {status}\n\n{comment}"

def make_vitamin_message():
    rate = get_vitamin_rate()
    if rate >= 70: status, comment = "🟢 충분", "비타민 섭취가 비교적 잘 이루어지고 있어요. 다만 당류가 함께 높은 음료인지도 확인해보세요."
    elif rate >= 30: status, comment = "🟡 보통", "비타민 섭취가 어느 정도 이루어졌어요. 비타민이 포함된 음료나 식품을 조금 더 고려해볼 수 있습니다."
    else: status, comment = "🔴 낮음", "오늘 비타민 섭취량이 낮은 편이에요. 비타민이 포함된 음료나 식품을 고려해보세요."

    return f"🍋 오늘 비타민\n\n오늘 섭취 비타민: {vitamin_today}mg\n목표 비타민: {vitamin_goal}mg\n목표 대비: {rate}%\n상태: {status}\n\n{comment}"

def make_recent_logs_message():
    text = "📈 오늘 통계 / 최근 음수 기록\n\n"
    if not recent_logs:
        return text + "• 아직 기록된 데이터가 없습니다. 웹캠 가이드라인에 성분표를 대고 캡처해 보세요!"
    for log in recent_logs:
        text += f"• {log['time']} / {log['drink']} / {log['amount']}ml\n  당류 {log['sugar']}g, 카페인 {log['caffeine']}mg, 비타민 {log.get('vitamin', 0)}mg\n  기록 방식: {log['source']}\n\n"
    return text

def make_no_drink_check_message():
    rate = get_water_rate()
    return f"⏰ 미섭취 상태 확인\n\n마지막 음수 이후 약 {last_drink_minutes}분이 지났어요.\n현재 달성률: {rate}%\n\n지금 물 한 잔을 마셔보는 건 어떨까요?"

def make_daily_summary_message():
    w_rate, s_rate, c_rate, v_rate = get_water_rate(), get_sugar_rate(), get_caffeine_rate(), get_vitamin_rate()
    return (
        f"📝 오늘의 음수 평가\n\n"
        f"💧 수분 달성률: {w_rate}% {get_water_emoji(w_rate)}\n"
        f"🍬 당류 섭취 상태: {s_rate}% {get_ingredient_emoji(s_rate)}\n"
        f"☕ 카페인 섭취 상태: {c_rate}% {get_ingredient_emoji(c_rate)}\n"
        f"🍋 비타민 섭취 상태: {v_rate}% {get_vitamin_emoji(v_rate)}\n\n"
        f"실시간 분석 데이터 연동 상태입니다."
    )

def make_auto_alert_test_message():
    return "🔔 자동 알림 조건 테스트\n\n현재 실시간 연동 연산 장치가 정상 가동 중입니다."

# =======================================================
# 6. 텔레그램 핸들러
# =======================================================
keyboard = [
    ["💧 오늘 음수량", "🍬 오늘 당류"],
    ["☕ 오늘 카페인", "🍋 오늘 비타민"],
    ["📈 오늘 통계", "⏰ 미섭취 확인"],
    ["📝 하루 평가", "🔔 자동 알림 테스트"],
    ["🌐 웹페이지"]
]
reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global user_chat_id
    user_chat_id = update.message.chat_id
    message = "안녕하세요!\n잘마시조 스마트 컵받침 알림 봇입니다 😊\n\n아래 버튼을 눌러 오늘의 음수 상태를 확인할 수 있어요."
    await update.message.reply_text(message, reply_markup=reply_markup)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global user_chat_id
    user_chat_id = update.message.chat_id
    text = update.message.text

    if text == "💧 오늘 음수량": await update.message.reply_text(make_water_message(), reply_markup=reply_markup)
    elif text == "🍬 오늘 당류": await update.message.reply_text(make_sugar_message(), reply_markup=reply_markup)
    elif text == "☕ 오늘 카페인": await update.message.reply_text(make_caffeine_message(), reply_markup=reply_markup)
    elif text == "🍋 오늘 비타민": await update.message.reply_text(make_vitamin_message(), reply_markup=reply_markup)
    elif text == "📈 오늘 통계": await update.message.reply_text(make_recent_logs_message(), reply_markup=reply_markup)
    elif text == "⏰ 미섭취 확인": await update.message.reply_text(make_no_drink_check_message(), reply_markup=reply_markup)
    elif text == "📝 하루 평가": await update.message.reply_text(make_daily_summary_message(), reply_markup=reply_markup)
    elif text == "🔔 자동 알림 테스트": await update.message.reply_text(make_auto_alert_test_message(), reply_markup=reply_markup)
    elif text == "🌐 웹페이지":
        web_button = InlineKeyboardMarkup([[InlineKeyboardButton("잘마시조 웹페이지 열기", url=WEB_URL)]])
        await update.message.reply_text("아래 버튼을 누르면 잘마시조 웹페이지로 이동할 수 있습니다.", reply_markup=web_button)
    else:
        await update.message.reply_text("아래 버튼 중 하나를 선택해주세요.", reply_markup=reply_markup)

# =======================================================
# 7. 메인 실행 루프 (Mac 환경 호환 처리)
# =======================================================
def start_telegram_bot():
    global main_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_loop = loop
    
    # 텔레그램 봇 폴링 시작
    telegram_app.run_polling(close_loop=False)

def main():
    global telegram_app
    
    telegram_app = ApplicationBuilder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 [통합 엔진] 잘마시조 웹캠 및 텔레그램 동시 연동 시작!")

    # 텔레그램 봇을 백그라운드 스레드로 실행
    bot_thread = threading.Thread(target=start_telegram_bot, daemon=True)
    bot_thread.start()

    # 웹캠(OpenCV 화면)을 메인 스레드에서 직접 실행 (Mac 필수 조건)
    run_camera()

if __name__ == "__main__":
    main()