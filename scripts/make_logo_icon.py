"""יוצר את אייקון הבאנר (images/carbot-icon.png) מתמונת הלוגו images/carbot-logo.jpg: חיתוך המכונית עם הרובוט.
הפרח בתמונה המקורית אינו לוגו שטראוס המדויק, ולכן נמחק; במקומו הבאנר מציג את הלוגו האמיתי (images/strauss-logo.png).
הרצה:  python scripts/make_logo_icon.py   (אם מחליפים את התמונה, ייתכן שצריך לכוונן את קואורדינטות החיתוך)"""
import os

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CROP = (446, 282, 770, 502)                                        # מכונית + רובוט (בתמונה 1024x1024)
ERASES = [(250, 270, 452, 505), (452, 270, 467, 413), (452, 395, 474, 414)]       # הפרח המקורי ושאריותיו - נצבעים בלבן


def main() -> None:
    image = Image.open(os.path.join(BASE, "images", "carbot-logo.jpg")).convert("RGB")
    draw = ImageDraw.Draw(image)
    for box in ERASES:
        draw.rectangle(box, fill=(255, 255, 255))
    icon = image.crop(CROP).convert("RGBA")
    width, height = icon.size
    for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):      # הרקע הלבן החיצוני הופך שקוף
        ImageDraw.floodfill(icon, corner, (255, 255, 255, 0), thresh=22)
    icon.save(os.path.join(BASE, "images", "carbot-icon.png"), optimize=True)
    print("נוצר images/carbot-icon.png")


if __name__ == "__main__":
    main()
