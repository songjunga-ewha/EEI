import cv2

from color_detector import analyze_image_file


# ------------------------------------------------------------
# 테스트할 이미지 파일
# ------------------------------------------------------------
#
# 일단 프로젝트 폴더에 테스트 사진을 하나 넣고
# 아래 파일 이름을 그 사진 이름으로 바꾼다.
#
# 예:
# brown_test.jpg
# ------------------------------------------------------------

IMAGE_PATH = "brown_test.jpg"


result = analyze_image_file(
    IMAGE_PATH
)


print()
print("========================================")
print("      잘마시조 색상 분석 테스트")
print("========================================")
print()

print("분석 성공 여부 :", result["ok"])
print("대표 색상       :", result["color_name"])
print("색상 코드       :", result["color"])
print("색상 비율       :", result["score"], "%")
print("추천 음료       :", result["drinks"])
print("메시지          :", result["message"])

print()
print("========================================")