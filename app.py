import json
import queue
import re
import threading
from datetime import datetime


from flask import (
   Flask,
   Response,
   jsonify,
   redirect,
   render_template,
   request,
   session,
   stream_with_context,
   url_for,
)
from color_detector import analyze_uploaded_file

app = Flask(__name__)
app.secret_key = "smart_coaster_demo_secret"


saved_user = {
   "name": "사용자",
   "age": "20",
   "weight": 60,
   "gender": "female",
   "diseases": [],
   "user_type": "일반 사용자",
   "recommended_water": 2000,
   "caffeine_limit": 300,
   "sugar_limit": 50,
   "vitamin_goal": 100,
}


saved_drinks = [
   {
       "name": "물",
       "total_volume": 500,
       "total_caffeine": 0,
       "total_sugar": 0,
       "total_vitamin": 0,
   },
   {
       "name": "아메리카노",
       "total_volume": 355,
       "total_caffeine": 150,
       "total_sugar": 0,
       "total_vitamin": 0,
   },
   {
       "name": "라떼",
       "total_volume": 370,
       "total_caffeine": 130,
       "total_sugar": 18,
       "total_vitamin": 5,
   },
   {
       "name": "비타민워터",
       "total_volume": 500,
       "total_caffeine": 0,
       "total_sugar": 18,
       "total_vitamin": 80,
   },
   {
       "name": "이온음료",
       "total_volume": 250,
       "total_caffeine": 0,
       "total_sugar": 18,
       "total_vitamin": 10,
   },
   {
       "name": "믹스커피",
       "total_volume": 100,
       "total_caffeine": 35,
       "total_sugar": 4,
       "total_vitamin": 0,
   },
]

# ✅ 수정 2: 즐겨찾기에서도 사용할 수 있도록 영양정보 표를 전역으로 이동
DRINK_NUTRITION = {

    # 기존 음료
    "물": {
        "total_volume": 500,
        "total_caffeine": 0,
        "total_sugar": 0,
        "total_vitamin": 0,
    },

    "아메리카노": {
        "total_volume": 355,
        "total_caffeine": 150,
        "total_sugar": 0,
        "total_vitamin": 0,
    },

    "라떼": {
        "total_volume": 370,
        "total_caffeine": 130,
        "total_sugar": 18,
        "total_vitamin": 5,
    },

    "비타민워터": {
        "total_volume": 500,
        "total_caffeine": 0,
        "total_sugar": 18,
        "total_vitamin": 80,
    },

    "이온음료": {
        "total_volume": 250,
        "total_caffeine": 0,
        "total_sugar": 18,
        "total_vitamin": 10,
    },

    "믹스커피": {
        "total_volume": 100,
        "total_caffeine": 35,
        "total_sugar": 4,
        "total_vitamin": 0,
    },

 }


drink_logs = []


dashboard_subscribers = []
dashboard_subscribers_lock = threading.Lock()


def classify_user_type(diseases):
   if not diseases:
       return "일반 사용자"


   type_map = {
       "diabetes": "당류 섭취 주의",
       "hyperlipidemia": "당류·지방 섭취 주의",
       "cirrhosis": "카페인·당류 섭취 주의",
       "heart_failure": "수분 섭취량 조절 주의",
   }


   selected_types = [type_map[disease] for disease in diseases if disease in type_map]
   return ", ".join(selected_types) if selected_types else "일반 사용자"




def calculate_recommended_water(weight, age, gender, diseases):
   """
   성별, 체중, 나이, 질병을 반영한 하루 권장 음수량 계산

   남성: 체중 × 35mL
   여성: 체중 × 30mL

   나이 보정:
   13~18세: +200mL
   19~64세: +0mL
   65세 이상: +200mL

   질병 보정:
   당뇨: +300mL
   고지혈증: +0mL

   간경화/심부전:
   일반적인 자동 계산을 적용하지 않고
   의료진 권장량을 우선하도록 별도 처리
   """

   # 기본값
   if gender == "male":
       base_water = weight * 35
   else:
       base_water = weight * 30

   # 나이 보정
   age_adjustment = 0

   if 13 <= age <= 18:
       age_adjustment = 200
   elif age >= 65:
       age_adjustment = 200

   # 질병 보정
   disease_adjustment = 0

   if "diabetes" in diseases:
       disease_adjustment += 300

   if "hyperlipidemia" in diseases:
       disease_adjustment += 0

   # 최종 계산
   recommended_water = (
       base_water
       + age_adjustment
       + disease_adjustment
   )

   return int(round(recommended_water))


def get_water_warning(diseases):
    if "heart_failure" in diseases:
        return {
            "show": True,
            "type": "heart_failure",
            "title": "심부전 수분 섭취 주의",
            "message": "심부전은 상태에 따라 수분 섭취량을 제한해야 하는 경우가 있습니다. 의료진의 권장 섭취량을 우선하세요.",
        }

    if "cirrhosis" in diseases:
        return {
            "show": True,
            "type": "cirrhosis",
            "title": "간경화 수분 섭취 주의",
            "message": "간경화는 상태에 따라 수분 섭취량을 조절해야 할 수 있습니다. 의료진의 권장 섭취량을 우선하세요.",
        }

    return {
        "show": False,
        "type": "",
        "title": "",
        "message": "",
    }


def calculate_rate(value, limit):
   if limit == 0:
       return 0
   return round(value / limit * 100, 2)




def safe_float(value, default=0.0):
   try:
       return float(value)
   except (TypeError, ValueError):
       return default




def safe_int(value, default=0):
   try:
       return int(float(value))
   except (TypeError, ValueError):
       return default




def safe_list_get(values, index, default=""):
   if index < len(values):
       return values[index]
   return default




def clamp_rate(rate):
   if rate < 0:
       return 0
   if rate > 100:
       return 100
   return rate




def parse_numeric(value, default=0.0):
   if value is None:
       return default
   if isinstance(value, (int, float)):
       return float(value)
   match = re.search(r"-?\d+(?:\.\d+)?", str(value))
   if not match:
       return default
   try:
       return float(match.group())
   except ValueError:
       return default




def get_water_status(rate):
   if rate <= 50:
       return {
           "text": "부족",
           "color": "danger",
           "message": "수분 섭취가 부족해요. 물을 조금 더 마셔보세요.",
       }
   if rate <= 70:
       return {
           "text": "주의",
           "color": "warning",
           "message": "목표량에 가까워지고 있어요. 조금만 더 마시면 좋아요.",
       }
   return {
       "text": "양호",
       "color": "success",
       "message": "좋아요! 현재까지 안정적인 음수 습관을 유지하고 있어요.",
   }




def get_ingredient_status(rate):
   if rate >= 90:
       return "danger"
   if rate >= 70:
       return "warning"
   return "success"




def get_vitamin_status(rate):
   if rate >= 70:
       return "success"
   if rate >= 30:
       return "warning"
   return "danger"




def get_bar_color(rate):
   if rate <= 50:
       return "bar-danger"
   if rate <= 70:
       return "bar-warning"
   return "bar-success"




def calculate_consumed_ingredients(
   total_volume, consumed_amount, total_caffeine, total_sugar, total_vitamin
):
   if total_volume == 0:
       return {"ratio": 0, "caffeine": 0, "sugar": 0, "vitamin": 0}
   ratio = consumed_amount / total_volume
   return {
       "ratio": round(ratio * 100, 2),
       "caffeine": round(total_caffeine * ratio, 2),
       "sugar": round(total_sugar * ratio, 2),
       "vitamin": round(total_vitamin * ratio, 2),
   }




def make_daily_feedback(water_rate, caffeine_rate, sugar_rate, vitamin_rate):
   messages = []
   if water_rate < 50:
       messages.append("오늘은 수분 섭취가 부족한 편이에요. 물을 조금 더 자주 마셔보세요.")
   elif water_rate >= 100:
       messages.append("오늘 목표 음수량을 달성했어요. 좋은 음수 습관을 잘 유지하고 있어요.")
   else:
       messages.append("오늘 수분 섭취는 목표에 가까워지고 있어요.")


   if sugar_rate >= 90:
       messages.append("당류 섭취가 높은 편이라 단 음료는 줄이는 것이 좋아요.")
   elif sugar_rate >= 70:
       messages.append("당류 섭취가 기준에 가까워지고 있어요.")


   if caffeine_rate >= 90:
       messages.append("카페인 섭취가 높은 편이에요. 카페인 없는 음료도 고려해보세요.")
   elif caffeine_rate >= 70:
       messages.append("카페인 섭취가 기준에 가까워지고 있어요.")


   if vitamin_rate >= 70:
       messages.append("비타민 섭취는 비교적 잘 이루어졌어요.")
   elif vitamin_rate < 30:
       messages.append("비타민 섭취는 낮은 편이에요. 비타민이 포함된 음료나 식품을 고려해볼 수 있어요.")


   return " ".join(messages)




def get_drink_type_card(total_water, total_caffeine, total_sugar, total_vitamin):
   if not drink_logs:
       return {
           "emoji": "💧",
           "name": "아직 분석 전",
           "description": "아직 기록이 부족해요. 음료를 기록하면 오늘의 음수 유형을 알려드릴게요.",
       }


   today = datetime.now().strftime("%Y-%m-%d")

   today_logs = [
       log for log in drink_logs
       if log.get("date") == today
   ]

   log_count = len(today_logs)
   if log_count > 0:
       average_amount = total_water / log_count
   else:
       average_amount = 0

   water_amount = 0

   for log in today_logs:
       drink_name = log.get("drink", "")

       if "물" in drink_name and "비타민" not in drink_name:
           water_amount += safe_float(
               log.get("consumed_amount", 0)
           )

   water_ratio = (
       calculate_rate(water_amount, total_water)
       if total_water > 0
       else 0
   )

   if total_caffeine >= 210:
       return {
           "emoji": "☕",
           "name": "카페인 충전형",
           "description": "카페인 음료의 비중이 높은 편이에요. 다음 음료는 물이나 무카페인 음료를 선택해보는 것도 좋아요.",
       }
   if total_sugar >= 35:
       return {
           "emoji": "🍬",
           "name": "달달파워형",
           "description": "당류가 들어간 음료를 자주 마시는 편이에요. 단 음료를 조금 줄이고 물이나 무가당 음료를 섞어 마셔보세요.",
       }
   if total_vitamin >= 70:
       return {
           "emoji": "🍋",
           "name": "상큼비타민형",
           "description": "비타민이 포함된 음료를 자주 마시는 편이에요. 당류도 함께 확인하면서 균형 있게 마셔보세요.",
       }
   if water_ratio >= 70:
       return {
           "emoji": "💧",
           "name": "물먹는 하마형",
           "description": "물 섭취 비중이 높은 편이에요. 아주 좋은 음수 습관이에요. 지금처럼 유지해보세요.",
       }
   if average_amount >= 300:
       return {
           "emoji": "🌪️",
           "name": "폭풍흡입형",
           "description": "한 번 마실 때 많은 양을 몰아서 마시는 편이에요. 조금씩 나누어 마시면 더 안정적인 음수 습관을 만들 수 있어요.",
       }
   return {
       "emoji": "⏰",
       "name": "성실한 물시계형",
       "description": "일정한 간격으로 꾸준히 마시는 편이에요. 지금처럼 규칙적인 음수 습관을 유지해보세요.",
   }




def get_drink_ranking():
   ranking = {}

   for log in drink_logs:
       name = log.get("drink", "이름 없는 음료")

       if not name:
           name = "이름 없는 음료"

       ranking[name] = ranking.get(name, 0) + 1

   sorted_ranking = sorted(
       ranking.items(),
       key=lambda item: (-item[1], item[0])
   )

   result = []
   current_rank = 0
   previous_count = None

   for index, (name, count) in enumerate(sorted_ranking, start=1):

       if count != previous_count:
           current_rank = index

       result.append({
           "rank": current_rank,
           "name": name,
           "count": count,
       })

       previous_count = count

   return result



def get_monthly_data(target_water):
    today = datetime.now()
    current_year = today.year
    current_month = today.month
    current_day = today.day

    daily_amounts = {}

    for log in drink_logs:
        date_text = log.get("date")

        if not date_text:
            continue

        try:
            log_date = datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            continue

        # 현재 연도와 현재 월의 기록만 사용
        if (
            log_date.year == current_year
            and log_date.month == current_month
        ):
            day = log_date.day
            amount = safe_int(log.get("consumed_amount", 0))

            daily_amounts[day] = daily_amounts.get(day, 0) + amount

    monthly_data = []

    # 오늘까지의 날짜만 표시
    for day in range(1, current_day + 1):
        amount = daily_amounts.get(day, 0)
        rate = calculate_rate(amount, target_water)

        if rate < 50:
            status = "부족"
        elif rate < 70:
            status = "주의"
        else:
            status = "양호"

        monthly_data.append({
            "day": day,
            "amount": amount,
            "rate": rate,
            "height": clamp_rate(rate),
            "color": get_bar_color(rate),
            "status": status,
        })

    return monthly_data

def get_reversed_logs_with_index():
   result = []
   for index in range(len(drink_logs) - 1, -1, -1):
       log = drink_logs[index].copy()
       log["real_index"] = index
       result.append(log)
   return result




def save_user_and_drinks(form):
   global saved_user
   global saved_drinks


   name = form.get("name", "")
   age = safe_int(form.get("age"), 20)
   weight = safe_float(form.get("weight"), 0)
   gender = form.get("gender", "female")
   diseases = form.getlist("disease")


   recommended_water = (
       calculate_recommended_water(
           weight,
           age,
           gender,
           diseases
       )
       if weight > 0
       else 2000
   )

   user_type = classify_user_type(diseases)

   drink_names = form.getlist("drink_name")
   drink_amounts = form.getlist("drink_amount")
   drink_caffeines = form.getlist("drink_caffeine")
   drink_sugars = form.getlist("drink_sugar")
   drink_vitamins = form.getlist("drink_vitamin")


   saved_drinks = [
      {
          "name": "물",
          "total_volume": 500,
          "total_caffeine": 0,
          "total_sugar": 0,
          "total_vitamin": 0,
      },
      {
          "name": "아메리카노",
          "total_volume": 355,
          "total_caffeine": 150,
          "total_sugar": 0,
          "total_vitamin": 0,
      },
      {
          "name": "라떼",
          "total_volume": 370,
          "total_caffeine": 130,
          "total_sugar": 18,
          "total_vitamin": 5,
      },
      {
          "name": "비타민워터",
          "total_volume": 500,
          "total_caffeine": 0,
          "total_sugar": 18,
          "total_vitamin": 80,
      },
      {
          "name": "이온음료",
          "total_volume": 250,
          "total_caffeine": 0,
          "total_sugar": 18,
          "total_vitamin": 10,
      },
      {
          "name": "믹스커피",
          "total_volume": 100,
          "total_caffeine": 35,
          "total_sugar": 4,
          "total_vitamin": 0,
      },


   ]
   

   for index in range(len(drink_names)):
       drink_name = safe_list_get(drink_names, index, "").strip()
       if drink_name == "":
           continue
       already_exists = any(
          drink.get("name") == drink_name
          for drink in saved_drinks
       )

       if not already_exists:
          drink = {
              "name": drink_name,
              "total_volume": safe_int(
                  safe_list_get(drink_amounts, index, 0)
              ),
              "total_caffeine": safe_int(
                  safe_list_get(drink_caffeines, index, 0)
              ),
              "total_sugar": safe_int(
                  safe_list_get(drink_sugars, index, 0)
              ),
              "total_vitamin": safe_int(
                  safe_list_get(drink_vitamins, index, 0)
              ),
          }
          saved_drinks.append(drink)

 
   saved_user = {
       "name": name if name else "사용자",
       "age": age if age > 0 else 20,
       "gender": gender,
       "weight": weight if weight > 0 else 60,
       "gender": gender,
       "diseases": diseases,
       "user_type": user_type,
       "recommended_water": recommended_water,
       "caffeine_limit": 300,
       "sugar_limit": 50,
       "vitamin_goal": 100,
   }




def get_dashboard_context():
   user_info = saved_user if saved_user is not None else {
       "name": "사용자",
       "age": "20",
       "gender": "female",
       "weight": 60,
       "diseases": [],
       "user_type": "일반 사용자",
       "recommended_water": 2000,
       "caffeine_limit": 300,
       "sugar_limit": 50,
       "vitamin_goal": 100,
   }

   today = datetime.now().strftime("%Y-%m-%d")

   total_water = sum(
       log.get("consumed_amount", 0)
       for log in drink_logs
       if log.get("date") == today
   )
   
   total_caffeine = round(sum(
       log.get("caffeine", 0)
       for log in drink_logs
       if log.get("date") == today
   ), 2)
   
   total_sugar = round(sum(
       log.get("sugar", 0)
       for log in drink_logs
       if log.get("date") == today
   ), 2)

   total_vitamin = round(sum(
       log.get("vitamin", 0)
       for log in drink_logs
       if log.get("date") == today
   ), 2)

   target_water = user_info.get("recommended_water", 2000)

   water_warning = get_water_warning(user_info.get("diseases", []))

   water_rate = calculate_rate(total_water, target_water)
   water_rate_display = clamp_rate(water_rate)
   caffeine_rate = calculate_rate(total_caffeine, user_info.get("caffeine_limit", 300))
   sugar_rate = calculate_rate(total_sugar, user_info.get("sugar_limit", 50))
   vitamin_rate = calculate_rate(total_vitamin, user_info.get("vitamin_goal", 100))

   water_status = get_water_status(water_rate)
   caffeine_status = get_ingredient_status(caffeine_rate)
   sugar_status = get_ingredient_status(sugar_rate)
   vitamin_status = get_vitamin_status(vitamin_rate)

   daily_feedback = make_daily_feedback(water_rate, caffeine_rate, sugar_rate, vitamin_rate)
   drink_type = get_drink_type_card(total_water, total_caffeine, total_sugar, total_vitamin)

   return {
       "user": user_info,
       "water_warning": water_warning,
       "drinks": saved_drinks,
       "logs": drink_logs,
       "reversed_logs": get_reversed_logs_with_index(),
       "ranking": get_drink_ranking(),
       "monthly_data": get_monthly_data(target_water),
       "daily_feedback": daily_feedback,
       "drink_type": drink_type,
       "total_water": total_water,
       "total_caffeine": total_caffeine,
       "total_sugar": total_sugar,
       "total_vitamin": total_vitamin,
       "water_rate": water_rate,
       "water_rate_display": water_rate_display,
       "caffeine_rate": caffeine_rate,
       "sugar_rate": sugar_rate,
       "vitamin_rate": vitamin_rate,
       "water_status": water_status,
       "caffeine_status": caffeine_status,
       "sugar_status": sugar_status,
       "vitamin_status": vitamin_status,
   }




def notify_dashboard_update():
   event = {
       "type": "dashboard_update",
       "time": datetime.now().isoformat(),
   }


   dead_subscribers = []
   with dashboard_subscribers_lock:
       for subscriber in dashboard_subscribers:
           try:
               subscriber.put_nowait(event)
           except queue.Full:
               dead_subscribers.append(subscriber)


       for subscriber in dead_subscribers:
           if subscriber in dashboard_subscribers:
               dashboard_subscribers.remove(subscriber)


@app.route("/", methods=["GET", "POST"])
@app.route("/index", methods=["GET", "POST"])
def index():
   if request.method == "POST":
       save_user_and_drinks(request.form)
       return redirect(url_for("dashboard"))


   return render_template("index.html")




@app.route("/dashboard")
def dashboard():

    context = get_dashboard_context()

    context["favorites"] = session.get(
        "favorites",
        []
    )

    return render_template(
        "dashboard.html",
        **context
    )



@app.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        save_user_and_drinks(request.form)
        notify_dashboard_update()
        return redirect(url_for("dashboard"))

    user_info = saved_user if saved_user is not None else {
        "name": "사용자",
        "age": "20",
        "gender": "female",
        "weight": 60,
        "diseases": [],
        "user_type": "일반 사용자",
        "recommended_water": 2000
    }

    return render_template("profile.html", user=user_info, drinks=saved_drinks)



@app.route("/logs")
def logs_page():
   context = get_dashboard_context()
   return render_template("logs.html", **context)



@app.route("/favorite", methods=["GET", "POST"])
def favorite():


    if request.method == "GET":

        favorites = session.get(
            "favorites",
            []
        )

        return render_template(
            "favorite.html",
            favorites=favorites
        )


    uploaded_file = request.files.get(
        "drink_image"
    )



    if uploaded_file is None:

        favorites = session.get(
            "favorites",
            []
        )

        return render_template(
            "favorite.html",
            favorites=favorites,
            error="음료 사진을 선택해주세요."
        )



    if uploaded_file.filename == "":

        favorites = session.get(
            "favorites",
            []
        )

        return render_template(
            "favorite.html",
            favorites=favorites,
            error="음료 사진을 선택해주세요."
        )


    analysis_result = analyze_uploaded_file(
        uploaded_file
    )



    if not analysis_result.get("ok"):

        favorites = session.get(
            "favorites",
            []
        )

        return render_template(
            "favorite.html",
            favorites=favorites,
            analysis=analysis_result,
            error=analysis_result.get(
                "message",
                "이미지 분석에 실패했습니다."
            )
        )


    favorites = session.get(
        "favorites",
        []
    )


    return render_template(
        "favorite.html",
        favorites=favorites,
        analysis=analysis_result
    )


@app.route(
    "/favorite/add",
    methods=["POST"]
)
def favorite_add():

    drink_name = request.form.get(
        "drink_name",
        ""
    ).strip()

    color = request.form.get(
        "color",
        ""
    ).strip()

    if not drink_name:
        return redirect(
            url_for("favorite")
        )


    favorites = session.get(
        "favorites",
        []
    )

    already_exists = any(
        favorite.get("name") == drink_name
        for favorite in favorites
    )

    if not already_exists:
        favorites.append(
            {
                "name": drink_name,
                "color": color,
            }
        )

    session["favorites"] = favorites
    session.modified = True



    drink_exists = any(
        drink.get("name") == drink_name
        for drink in saved_drinks
    )


    if not drink_exists:

        nutrition = DRINK_NUTRITION.get(
            drink_name,
            {
                "total_volume": 500,
                "total_caffeine": 0,
                "total_sugar": 0,
                "total_vitamin": 0,
            }
        )

        new_drink = {
            "name": drink_name,
            "total_volume": nutrition["total_volume"],
            "total_caffeine": nutrition["total_caffeine"],
            "total_sugar": nutrition["total_sugar"],
            "total_vitamin": nutrition["total_vitamin"],
        }

        saved_drinks.append(
            new_drink
        )



    return redirect(
        url_for("favorite")
    )


@app.route(
    "/favorite/consume",
    methods=["POST"]
)
def favorite_consume():

    drink_name = request.form.get(
        "drink_name",
        ""
    ).strip()


    consumed_amount = parse_numeric(
        request.form.get(
            "consumed_amount",
            0
        )
    )

    if not drink_name or consumed_amount <= 0:
        return redirect(
            url_for("dashboard")
        )


    drink = next(
        (
            item
            for item in saved_drinks
            if item.get("name") == drink_name
        ),
        None
    )


    if drink is None:

        print(
            "즐겨찾기 음료 정보를 찾을 수 없습니다:",
            drink_name
        )

        print(
            "현재 saved_drinks:",
            saved_drinks
        )

        return redirect(
            url_for("dashboard")
        )


    total_volume = safe_float(
        drink.get(
            "total_volume",
            0
        )
    )

    if total_volume <= 0:

        return redirect(
            url_for("dashboard")
        )

    consumed_amount = min(
        consumed_amount,
        total_volume
    )



    calculated = calculate_consumed_ingredients(

        total_volume=total_volume,

        consumed_amount=consumed_amount,

        total_caffeine=safe_float(
            drink.get(
                "total_caffeine",
                0
            )
        ),

        total_sugar=safe_float(
            drink.get(
                "total_sugar",
                0
            )
        ),

        total_vitamin=safe_float(
            drink.get(
                "total_vitamin",
                0
            )
        ),
    )


    new_log = {

        "date":
            datetime.now().strftime(
                "%Y-%m-%d"
            ),

        "time":
            datetime.now().strftime(
                "%H:%M"
            ),

        "drink":
            drink_name,

        "total_volume":
            round(
                total_volume,
                2
            ),

        "consumed_amount":
            round(
                consumed_amount,
                2
            ),

        "caffeine":
            calculated["caffeine"],

        "sugar":
            calculated["sugar"],

        "vitamin":
            calculated["vitamin"],

        "drink_ratio":
            calculated["ratio"],

        "source":
            "즐겨찾기 음료 수동 기록",

        "status":
            "정상",
    }

    drink_logs.append(
        new_log
    )


    notify_dashboard_update()

    return redirect(
        url_for("dashboard")
    )



@app.route(
    "/favorite/delete/<int:favorite_index>",
    methods=["POST"]
)
def favorite_delete(favorite_index):



    favorites = session.get(
        "favorites",
        []
    )


    if (
        0 <= favorite_index
        < len(favorites)
    ):

        favorites.pop(
            favorite_index
        )


    session["favorites"] = favorites

    session.modified = True


    return redirect(
        url_for("favorite")
    )







@app.route("/delete_log/<int:log_index>", methods=["POST"])
def delete_log(log_index):
   if 0 <= log_index < len(drink_logs):
       drink_logs.pop(log_index)
       notify_dashboard_update()


   return redirect(url_for("logs_page"))



@app.route("/api/add_drink_log", methods=["POST"])
@app.route("/api/add_log", methods=["POST"])
def api_add_drink_log():
   data = request.get_json(silent=True)


   if not data:
       return jsonify({"ok": False, "error": "JSON 데이터가 없습니다."}), 400


   total_volume = parse_numeric(data.get("total_volume", data.get("volume", 0)))
   consumed_amount = parse_numeric(data.get("consumed_amount", data.get("amount", 0)))


   if total_volume <= 0:
       return jsonify({"ok": False, "error": "전체 용량이 올바르지 않습니다."}), 400


   if consumed_amount < 0:
       consumed_amount = 0
   if consumed_amount > total_volume:
       consumed_amount = total_volume


   total_caffeine = parse_numeric(data.get("total_caffeine", data.get("caffeine", 0)))
   total_sugar = parse_numeric(data.get("total_sugar", data.get("sugar", 0)))
   total_vitamin = parse_numeric(data.get("total_vitamin", data.get("vitamin", 0)))


   calculated = calculate_consumed_ingredients(
       total_volume=total_volume,
       consumed_amount=consumed_amount,
       total_caffeine=total_caffeine,
       total_sugar=total_sugar,
       total_vitamin=total_vitamin,
   )


   new_log = {
       "date": data.get("date", datetime.now().strftime("%Y-%m-%d")),
       "time": data.get("time", datetime.now().strftime("%H:%M")),
       "drink": data.get("drink", "인식된 음료"),
       "total_volume": round(total_volume, 2),
       "consumed_amount": round(consumed_amount, 2),
       "caffeine": calculated["caffeine"],
       "sugar": calculated["sugar"],
       "vitamin": calculated["vitamin"],
       "drink_ratio": calculated["ratio"],
       "source": data.get("source", "OCR + 로드셀 측정"),
       "status": data.get("status", "실시간 측정 반영"),
   }


   drink_logs.append(new_log)
   notify_dashboard_update()


   return jsonify({"ok": True, "message": "음수 기록이 추가되었습니다.", "log": new_log}), 200




@app.route("/api/dashboard_data", methods=["GET"])
def api_dashboard_data():
   context = get_dashboard_context()
   user_info = saved_user if saved_user is not None else {"recommended_water": 2000}


   return jsonify({
       "ok": True,
       "water_warning": context["water_warning"],
       "total_water": context["total_water"],
       "recommended_water": user_info.get("recommended_water", 2000),
       "water_rate": context["water_rate"],
       "water_rate_display": context["water_rate_display"],
       "total_caffeine": context["total_caffeine"],
       "total_sugar": context["total_sugar"],
       "total_vitamin": context["total_vitamin"],
       "caffeine_status": context["caffeine_status"],
       "sugar_status": context["sugar_status"],
       "vitamin_status": context["vitamin_status"],
       "water_status": context["water_status"],
       "daily_feedback": context["daily_feedback"],
       "latest_logs": context["reversed_logs"][:3],
       "drink_type": context["drink_type"],
       "ranking": context["ranking"][:3],
       "monthly_data": context["monthly_data"],
   })




@app.route("/api/user_settings", methods=["GET"])
def api_user_settings():
   return jsonify({
       "ok": True,
       "name": saved_user["name"],
       "weight": saved_user["weight"],
       "recommended_water": saved_user["recommended_water"],
       "caffeine_limit": saved_user["caffeine_limit"],
       "sugar_limit": saved_user["sugar_limit"],
       "vitamin_goal": saved_user["vitamin_goal"],
   }), 200



@app.route("/api/dashboard_events", methods=["GET"])
def api_dashboard_events():
   subscriber = queue.Queue(maxsize=10)

   with dashboard_subscribers_lock:
       dashboard_subscribers.append(subscriber)

   @stream_with_context
   def event_stream():
       try:
           connected_message = {"type": "connected"}

           yield (
               "data: "
               + json.dumps(
                   connected_message,
                   ensure_ascii=False
               )
               + "\n\n"
           )

           while True:
               try:
                   event = subscriber.get(timeout=20)

                   yield (
                       "data: "
                       + json.dumps(
                           event,
                           ensure_ascii=False
                       )
                       + "\n\n"
                   )

               except queue.Empty:
                   heartbeat = {
                       "type": "heartbeat"
                   }

                   yield (
                       "data: "
                       + json.dumps(
                           heartbeat,
                           ensure_ascii=False
                       )
                       + "\n\n"
                   )

       except GeneratorExit:
           pass

       finally:
           with dashboard_subscribers_lock:
               if subscriber in dashboard_subscribers:
                   dashboard_subscribers.remove(subscriber)

   return Response(
       event_stream(),
       mimetype="text/event-stream",
       headers={
           "Cache-Control": "no-cache",
           "X-Accel-Buffering": "no",
           "Connection": "keep-alive",
       },
   )
  

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )