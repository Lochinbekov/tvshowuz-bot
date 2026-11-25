# TvShowUz Bot

Telegram bot: ko'rgan seriallaringizni epizod-epizod baholab boradi va natijani kanal yoki guruhga avtomatik yozib qo'yadi.

## Imkoniyatlar

- Serial qidirish va qo'shish
- Har bir epizodni 1 dan 10 gacha baholash
- Baholar kanal yoki guruhga avtomatik yuboriladi
- Oldin ko'rgan epizod yoki faslni belgilash (kanalga yuborilmaydi, statistikaga qo'shiladi)
- Serial yoqmasa to'xtatish: statistikasi kanalga yuboriladi
- Serial tugaganda yakuniy statistika rasmlari
- Umumiy statistika va tavsiyalar
- Seriallarni do'stga ulashish

## Buyruqlar

| Buyruq | Vazifasi |
| --- | --- |
| /start | Botni boshlash |
| /menu | Asosiy menyu |
| /my | Seriallarim |
| /stats | Statistika |
| /help | Yordam |

## O'rnatish

Python 3.10 yoki undan yangisi kerak.

1. Loyihani yuklab oling:

```bash
git clone https://github.com/Lochinbekov/tvshowuz-bot.git
cd tvshowuz-bot
```

2. Kutubxonalarni o'rnating:

```bash
pip install -r requirements.txt
```

3. `.env` fayl yarating va ichiga yozing:

```env
BOT_TOKEN=bot_tokeningiz
OMDB_API_KEY=omdb_kalitingiz
```

4. Botni ishga tushiring:

```bash
python bot.py
```

## Sozlamalar (.env)

| Nomi | Majburiy | Tavsif |
| --- | --- | --- |
| BOT_TOKEN | Ha | Telegram bot tokeni. @BotFather dan olinadi |
| OMDB_API_KEY | Yo'q | IMDb reytinglari uchun kalit. omdbapi.com saytidan bepul olinadi |
| TIMEZONE | Yo'q | Vaqt mintaqasi. Standart: Asia/Tashkent |
| DB_PATH | Yo'q | Baza fayli yo'li. Standart: tvbot.db |
| STALE_DAYS | Yo'q | Necha kundan keyin serial "chala qolgan" hisoblanadi. Standart: 14 |

## Kanalga ulash

1. Botni kanal yoki guruhga qo'shing va admin qiling (xabar yuborish, tahrirlash va o'chirish huquqi bilan).
2. Botdagi menyudan "Kanal / guruh" bo'limiga kirib, kerakli kanalni tanlang.
3. Endi baholagan epizodlaringiz shu kanalga yuboriladi.

## Ma'lumot manbalari

- Serial va epizodlar: [TVMaze](https://www.tvmaze.com)
- Reytinglar: [IMDb](https://www.imdb.com) ([OMDb API](https://www.omdbapi.com) orqali)

## Litsenziya

MIT. Batafsil [LICENSE](LICENSE) faylida.

## Muallif

Lochinbekov. Telegram: [@Lochinbekovv](https://t.me/Lochinbekovv)
