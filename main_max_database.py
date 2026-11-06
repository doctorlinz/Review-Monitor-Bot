import asyncio
import aiohttp
import sqlite3
import os
import html
import time
import re
import hashlib
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# === Конфигурация ===
API_KEY = "6e7e1929-4ea9-4a5d-8c05-d601860389bd"
BRANCH_URLS = [""]                       # 2GIS отключён
YANDEX_IDS = [1654386580]
DB_FILE = "reviews.db"

# === MAX Messenger ===
MAX_API_URL = "https://botapi.max.ru"
MAX_TOKEN   = "f9LHodD0cOIy_sCEc7v_mcTeGdQOpZBa8Kp2xiyw37lBGKvK-8yS5xoi-2p-QTdLz96PFRxukRLRBAZcDoQ7"
MAX_CHAT_ID = -80919322747499                          # ← заменить (числовой chat_id)

# === Класс для работы с базой данных SQLite (без изменений) ===
class Database:
    def __init__(self, db_file):
        self.db_file = db_file
        self.init_db()

    def init_db(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gis_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT UNIQUE,
                address TEXT,
                date TEXT,
                author TEXT,
                rating REAL,
                text TEXT,
                answer TEXT,
                answer_date TEXT,
                sent INTEGER DEFAULT 0,
                last_updated TEXT,
                edit_count INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS yandex_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id TEXT UNIQUE,
                address TEXT,
                date TEXT,
                author TEXT,
                rating REAL,
                text TEXT,
                answer TEXT,
                sent INTEGER DEFAULT 0,
                last_updated TEXT,
                edit_count INTEGER DEFAULT 0
            )
        ''')
        conn.commit()
        conn.close()

    def check_review_changed(self, source, review_id, new_data):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            if source == "2gis":
                cursor.execute("SELECT text, answer, rating FROM gis_reviews WHERE review_id = ?", (review_id,))
            else:
                cursor.execute("SELECT text, answer, rating FROM yandex_reviews WHERE review_id = ?", (review_id,))
            existing_review = cursor.fetchone()
            if existing_review:
                existing_text, existing_answer, existing_rating = existing_review
                if (existing_text != new_data['text'] or
                    existing_answer != new_data.get('answer', '') or
                    existing_rating != new_data['rating']):
                    return True
            return False
        except Exception as e:
            print(f"Ошибка при проверке изменений отзыва: {e}")
            return False
        finally:
            conn.close()

    def get_connection(self):
        return sqlite3.connect(self.db_file)

    def get_existing_ids(self, source):
        conn = self.get_connection()
        cursor = conn.cursor()
        if source == "2gis":
            cursor.execute("SELECT review_id FROM gis_reviews")
        else:
            cursor.execute("SELECT review_id FROM yandex_reviews")
        ids = set(row[0] for row in cursor.fetchall())
        conn.close()
        return ids

    def get_unsent_reviews(self, source):
        conn = self.get_connection()
        cursor = conn.cursor()
        if source == "2gis":
            cursor.execute("SELECT * FROM gis_reviews WHERE sent = 0 ORDER BY date")
        else:
            cursor.execute("SELECT * FROM yandex_reviews WHERE sent = 0 ORDER BY date")
        columns = [col[0] for col in cursor.description]
        reviews = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return reviews

    def save_review(self, source, review_data):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            if source == "2gis":
                cursor.execute("SELECT text, answer, last_updated FROM gis_reviews WHERE review_id = ?",
                               (review_data['review_id'],))
            else:
                cursor.execute("SELECT text, answer, last_updated FROM yandex_reviews WHERE review_id = ?",
                               (review_data['review_id'],))
            existing_review = cursor.fetchone()
            if existing_review:
                existing_text, existing_answer, existing_updated = existing_review
                current_text = review_data.get('text', '')
                current_answer = review_data.get('answer', '')
                if existing_text != current_text or existing_answer != current_answer:
                    if source == "2gis":
                        cursor.execute('''
                            UPDATE gis_reviews
                            SET address=?, date=?, author=?, rating=?, text=?, answer=?, answer_date=?,
                                last_updated=?, edit_count=edit_count+1, sent=0
                            WHERE review_id=?
                        ''', (
                            review_data['address'], review_data['date'], review_data['author'],
                            review_data['rating'], review_data['text'], review_data.get('answer', ''),
                            review_data.get('answer_date', ''), datetime.now().isoformat(),
                            review_data['review_id']
                        ))
                    else:
                        cursor.execute('''
                            UPDATE yandex_reviews
                            SET address=?, date=?, author=?, rating=?, text=?, answer=?,
                                last_updated=?, edit_count=edit_count+1, sent=0
                            WHERE review_id=?
                        ''', (
                            review_data['address'], review_data['date'], review_data['author'],
                            review_data['rating'], review_data['text'], review_data.get('answer', ''),
                            datetime.now().isoformat(), review_data['review_id']
                        ))
                    print(f"Обновлен отзыв {review_data['review_id']} (редактирование)")
                    conn.commit()
                    return True
                else:
                    return False
            else:
                if source == "2gis":
                    cursor.execute('''
                        INSERT INTO gis_reviews
                        (review_id, address, date, author, rating, text, answer, answer_date, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        review_data['review_id'], review_data['address'], review_data['date'],
                        review_data['author'], review_data['rating'], review_data['text'],
                        review_data.get('answer', ''), review_data.get('answer_date', ''),
                        datetime.now().isoformat()
                    ))
                else:
                    cursor.execute('''
                        INSERT INTO yandex_reviews
                        (review_id, address, date, author, rating, text, answer, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        review_data['review_id'], review_data['address'], review_data['date'],
                        review_data['author'], review_data['rating'], review_data['text'],
                        review_data.get('answer', ''), datetime.now().isoformat()
                    ))
                conn.commit()
                return True
        except Exception as e:
            print(f"Ошибка при сохранении отзыва: {e}")
            return False
        finally:
            conn.close()

    def mark_as_sent(self, source, review_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            if source == "2gis":
                cursor.execute("UPDATE gis_reviews SET sent = 1 WHERE review_id = ?", (review_id,))
            else:
                cursor.execute("UPDATE yandex_reviews SET sent = 1 WHERE review_id = ?", (review_id,))
            conn.commit()
            print(f"Отзыв {review_id} помечен как отправленный")
        except Exception as e:
            print(f"Ошибка при обновлении статуса отзыва: {e}")
        finally:
            conn.close()

db = Database(DB_FILE)

# === Классы для парсинга Яндекс (без изменений) ===
class ParserHelper:
    @staticmethod
    def get_count_star(stars_elements):
        count = 0
        for star in stars_elements:
            classes = star.get_attribute('class')
            if '_full' in classes:
                count += 1
            elif '_half' in classes:
                count += 0.5
        return count

    @staticmethod
    def form_date(date_str):
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S.%fZ')
            return dt.timestamp()
        except:
            return time.time()

    @staticmethod
    def format_rating(rating_elements):
        if rating_elements:
            try:
                return float(rating_elements[0].text.replace(',', '.'))
            except:
                return 0.0
        return 0.0

    @staticmethod
    def list_to_num(text):
        try:
            return int(re.sub(r'\D', '', text))
        except:
            return 0

class Parser:
    def __init__(self, driver):
        self.driver = driver
        self.wait = WebDriverWait(driver, 10)

    def __scroll_to_bottom(self, elem):
        self.driver.execute_script("arguments[0].scrollIntoView();", elem)
        time.sleep(1)
        new_elems = self.driver.find_elements(By.CLASS_NAME, "business-reviews-card-view__review")
        if new_elems and new_elems[-1] != elem:
            self.__scroll_to_bottom(new_elems[-1])

    def __get_data_item(self, elem):
        name = None
        for el in elem.find_elements(By.XPATH, ".//span[@itemprop='name']"):
            name = el.text
            break

        icon_href = None
        for el in elem.find_elements(By.XPATH, ".//div[contains(@class, 'user-icon-view__icon')]"):
            style = el.get_attribute('style') or ""
            m = re.findall(r'url\("?(.*?)"?\)', style)
            icon_href = m[0] if m else None
            break

        date = None
        for el in elem.find_elements(By.XPATH, ".//meta[@itemprop='datePublished']"):
            date = el.get_attribute('content')
            break

        text = None
        bodies = elem.find_elements(By.CLASS_NAME, "business-review-view__body")
        if bodies:
            body = bodies[0]
            for el in body.find_elements(By.CLASS_NAME, "spoiler-view__text-container"):
                text = el.text
                break
            if not text:
                for el in body.find_elements(By.XPATH, ".//span[contains(@class, 'business-review-view__body-text')]"):
                    text = el.text
                    break
            if not text:
                text = body.text

        stars_count = 0
        stars = elem.find_elements(By.XPATH, ".//div[contains(@class, 'business-rating-badge-view__stars')]/span")
        if stars:
            stars_count = ParserHelper.get_count_star(stars)

        answer = None
        btns = elem.find_elements(By.CLASS_NAME, "business-review-view__comment-expand")
        if btns:
            try:
                self.driver.execute_script("arguments[0].click()", btns[0])
                time.sleep(0.5)
                bubbles = elem.find_elements(By.CLASS_NAME, "business-review-comment-content__bubble")
                if bubbles:
                    answer = bubbles[0].text
            except Exception:
                pass

        return {
            'name': name,
            'icon_href': icon_href,
            'date': ParserHelper.form_date(date) if date else None,
            'text': text,
            'stars': stars_count,
            'answer': answer
        }

    def __get_data_company(self):
        try:
            name = self.driver.find_element(By.XPATH, ".//h1[contains(@class, 'orgpage-header-view__header')]").text
        except NoSuchElementException:
            name = None
        try:
            rating_block = self.driver.find_element(By.XPATH, ".//div[contains(@class, 'business-summary-rating-badge-view__rating-and-stars')]")
            rating_text = rating_block.find_elements(By.XPATH, ".//span[contains(@class, 'business-summary-rating-badge-view__rating-text')]")
            rating = ParserHelper.format_rating(rating_text)
            count_rating = rating_block.find_element(By.XPATH, ".//span[contains(@class, 'business-rating-amount-view')]").text
            count_rating = ParserHelper.list_to_num(count_rating)
            stars = rating_block.find_elements(By.XPATH, ".//div[contains(@class, 'business-rating-badge-view__stars')]/span")
            stars_count = ParserHelper.get_count_star(stars)
        except NoSuchElementException:
            rating, count_rating, stars_count = 0, 0, 0
        return {'name': name, 'rating': rating, 'count_rating': count_rating, 'stars': stars_count}

    def __get_data_reviews(self):
        reviews = []
        try:
            self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "business-reviews-card-view__review")))
            elements = self.driver.find_elements(By.CLASS_NAME, "business-reviews-card-view__review")
            if elements:
                self.__scroll_to_bottom(elements[-1])
                elements = self.driver.find_elements(By.CLASS_NAME, "business-reviews-card-view__review")
                for elem in elements:
                    reviews.append(self.__get_data_item(elem))
        except NoSuchElementException:
            pass
        return reviews

    def __is_valid_page(self):
        try:
            return bool(self.driver.find_element(By.XPATH, ".//h1[contains(@class, 'orgpage-header-view__header')]"))
        except NoSuchElementException:
            return False

    def parse_all_data(self):
        if not self.__is_valid_page():
            return {'error': 'Страница не найдена'}
        return {'company_info': self.__get_data_company(), 'company_reviews': self.__get_data_reviews()}

    def parse_reviews(self):
        if not self.__is_valid_page():
            return {'error': 'Страница не найдена'}
        return {'company_reviews': self.__get_data_reviews()}

    def parse_company_info(self):
        if not self.__is_valid_page():
            return {'error': 'Страница не найдена'}
        return {'company_info': self.__get_data_company()}

class YandexParser:
    def __init__(self, id_yandex: int):
        self.id_yandex = id_yandex

    def __open_page(self):
        url = f'https://yandex.ru/maps/org/doktor_linz/{self.id_yandex}/reviews/'
        chrome_options = Options()
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--disable-default-apps')
        chrome_options.add_argument('--disable-component-extensions-with-background-pages')
        chrome_options.add_argument('--disable-background-timer-throttling')
        chrome_options.add_argument('--disable-backgrounding-occluded-windows')
        chrome_options.add_argument('--disable-renderer-backgrounding')
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--log-level=3')
        chrome_options.add_argument('--disable-logging')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
        chrome_options.add_argument("--disable-dev-tools")
        prefs = {
            'profile.default_content_setting_values.notifications': 2,
            'profile.default_content_settings.popups': 0,
            'profile.managed_default_content_settings.durable_storage': 2,
            'credentials_enable_service': False,
            'password_manager_enabled': False,
            'profile.default_content_setting_values.geolocation': 2,
        }
        chrome_options.add_experimental_option('prefs', prefs)
        try:
            service = Service(log_path='NUL')
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except:
            driver = webdriver.Chrome(options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        parser = Parser(driver)
        driver.get(url)
        return parser

    def parse(self, parse_type: str = 'default'):
        result = {}
        parser = self.__open_page()
        time.sleep(5)
        try:
            if parse_type == 'default':
                result = parser.parse_all_data()
            elif parse_type == 'company':
                result = parser.parse_company_info()
            elif parse_type == 'reviews':
                result = parser.parse_reviews()
        except Exception as e:
            print(f"Ошибка при парсинге: {e}")
            result = {'error': str(e)}
        finally:
            parser.driver.quit()
            return result

# === Отправка в MAX (заменяет Telegram) ===
async def send_message(text: str):
    url = f"{MAX_API_URL}/messages"
    headers = {"Authorization": MAX_TOKEN}
    params = {"chat_id": MAX_CHAT_ID}
    payload = {"text": text, "format": "html", "notify": True}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, params=params, json=payload) as resp:
                if resp.status == 200:
                    return True
                body = await resp.text()
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    print(f"MAX rate limit, ждём {retry_after}с")
                    await asyncio.sleep(retry_after + 2)
                    return await send_message(text)
                print(f"Ошибка MAX: {resp.status} {body}")
                return False
    except Exception as e:
        print(f"Ошибка отправки в MAX: {e}")
        return False

# === Миграция БД (без изменений) ===
def migrate_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA table_info(gis_reviews)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'last_updated' not in columns:
            cursor.execute("ALTER TABLE gis_reviews ADD COLUMN last_updated TEXT")
        if 'edit_count' not in columns:
            cursor.execute("ALTER TABLE gis_reviews ADD COLUMN edit_count INTEGER DEFAULT 0")
        cursor.execute("PRAGMA table_info(yandex_reviews)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'last_updated' not in columns:
            cursor.execute("ALTER TABLE yandex_reviews ADD COLUMN last_updated TEXT")
        if 'edit_count' not in columns:
            cursor.execute("ALTER TABLE yandex_reviews ADD COLUMN edit_count INTEGER DEFAULT 0")
        conn.commit()
    except Exception as e:
        print(f"Ошибка при миграции базы данных: {e}")
    finally:
        conn.close()

# === Форматирование (без изменений) ===
def format_datetime(dt_value):
    try:
        if isinstance(dt_value, str) and dt_value.replace('.', '', 1).isdigit():
            dt_value = float(dt_value) if '.' in dt_value else int(dt_value)
        if isinstance(dt_value, (int, float)):
            if dt_value > 1e10:
                dt_value = dt_value / 1000
            dt = datetime.fromtimestamp(dt_value, timezone.utc)
        else:
            if isinstance(dt_value, str):
                if 'T' in dt_value:
                    if dt_value.endswith('Z'):
                        dt = datetime.fromisoformat(dt_value[:-1] + '+00:00')
                    else:
                        dt = datetime.fromisoformat(dt_value)
                else:
                    dt = datetime.strptime(dt_value, '%Y-%m-%d %H:%M:%S')
            else:
                return str(dt_value)
        tz = timezone(timedelta(hours=4))
        dt = dt.astimezone(tz)
        return dt.strftime("%H:%M:%S %d.%m.%Y")
    except (ValueError, AttributeError) as e:
        print(f"Ошибка форматирования даты: {e}, значение: {dt_value}")
        return str(dt_value)

def format_review(review, source="2GIS"):
    rating = review['rating']
    if isinstance(rating, float) and rating.is_integer():
        rating_str = str(int(rating))
    else:
        rating_str = str(rating)
    escaped_text = html.escape(review['text'])
    if len(escaped_text) > 4000:
        escaped_text = escaped_text[:4000] + "..."
    review_date = format_datetime(review['date'])
    is_edited = review.get('edit_count', 0) > 0
    edit_info = ""
    if is_edited and review.get('last_updated'):
        edit_date = format_datetime(review['last_updated'])
        edit_info = f"<b>✏️ Дата редактирования:</b> {edit_date}\n"
    if source == "2GIS":
        message = (f"<b>📝 Отзыв с 2ГИС</b>\n\n"
                  f"<b>🏢 Адрес:</b> {html.escape(review['address'])}\n"
                  f"<b>📅 Дата отзыва:</b> {review_date}\n"
                  f"{edit_info}"
                  f"<b>👤 Автор:</b> {html.escape(review['author'])}\n"
                  f"<b>⭐ Рейтинг:</b> {rating_str}/5\n\n"
                  f"<b>💬 Текст:</b>\n{escaped_text}\n\n")
        if review.get('answer'):
            escaped_answer = html.escape(review['answer'])
            if len(escaped_answer) > 4000:
                escaped_answer = escaped_answer[:4000] + "..."
            answer_date = format_datetime(review.get('answer_date', '')) if review.get('answer_date') else "не указана"
            message += (f"<b>📣 Ответ компании:</b>\n{escaped_answer}\n"
                       f"<b>📅 Дата ответа:</b> {answer_date}\n\n")
        return message
    else:
        message = (f"<b>📝 Отзыв с Яндекс Карт</b>\n\n"
                  f"<b>🏢 Организация:</b> {html.escape(review['address'])}\n"
                  f"<b>📅 Дата отзыва:</b> {review_date}\n"
                  f"{edit_info}"
                  f"<b>👤 Автор:</b> {html.escape(review['author'])}\n"
                  f"<b>⭐ Рейтинг:</b> {rating_str}/5\n\n"
                  f"<b>💬 Текст:</b>\n{escaped_text}\n\n")
        if review.get('answer'):
            escaped_answer = html.escape(review['answer'])
            if len(escaped_answer) > 4000:
                escaped_answer = escaped_answer[:4000] + "..."
            message += f"<b>📣 Ответ компании:</b>\n{escaped_answer}\n\n"
        return message

# === 2GIS (оставлено для совместимости, но BRANCH_URLS пуст) ===
async def get_page_title(session, url):
    try:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
            html_content = await resp.text()
            soup = BeautifulSoup(html_content, "html.parser")
            return soup.title.string.strip().removesuffix(" — 2ГИС")
    except Exception:
        return "Ошибка получения адреса"

async def fetch_reviews(session, url, existing_ids):
    firm_id = url.split("/")[-1]
    address = await get_page_title(session, url)
    print(f"Ищем отзывы предприятия: {address}")
    new_reviews = []
    updated_reviews = []
    limit = 50
    offset = 0
    while True:
        api_url = f"https://public-api.reviews.2gis.com/2.0/branches/{firm_id}/reviews"
        params = {
            "limit": limit, "offset": offset, "is_advertiser": "false",
            "fields": "meta.providers,meta.branch_rating,meta.branch_reviews_count,meta.total_count,reviews.hiding_reason,reviews.is_verified,reviews.emojis",
            "without_my_first_review": "false", "rated": "true", "sort_by": "date_edited",
            "key": API_KEY, "locale": "ru_RU"
        }
        try:
            async with session.get(api_url, params=params) as resp:
                data = await resp.json()
                reviews = data.get("reviews", [])
                if not reviews:
                    break
                for review in reviews:
                    rid = review["id"]
                    official_answer = review.get("official_answer")
                    answer_text = ""
                    answer_date = ""
                    if official_answer:
                        answer_text = official_answer.get("text", "")
                        answer_date = official_answer.get("date_created", "")
                    original_date = review.get("date_edited") or review.get("date_created")
                    if original_date:
                        dt = datetime.fromisoformat(original_date.replace('Z', '+00:00'))
                        corrected_date = int(dt.timestamp() * 1000)
                    else:
                        corrected_date = None
                    review_data = {
                        "review_id": rid, "address": address, "date": corrected_date,
                        "author": review["user"]["name"], "rating": review.get("rating", ""),
                        "text": review.get("text", "").replace("\n", " ").strip(),
                        "answer": answer_text, "answer_date": answer_date
                    }
                    if rid in existing_ids:
                        if db.check_review_changed("2gis", rid, review_data):
                            updated_reviews.append(review_data)
                    else:
                        new_reviews.append(review_data)
                if len(reviews) < limit:
                    break
                offset += limit
                await asyncio.sleep(1)
        except Exception as e:
            print(f"Ошибка при запросе {url}: {e}")
            break
    for review in new_reviews + updated_reviews:
        db.save_review("2gis", review)
    return new_reviews, updated_reviews

# === Яндекс ===
def parse_yandex_reviews(company_id, existing_ids):
    parser = YandexParser(company_id)
    data = parser.parse('reviews')
    if 'error' in data:
        print(f"Ошибка парсинга Яндекс: {data['error']}")
        return [], []
    reviews_data = data.get('company_reviews', [])
    new_reviews = []
    new_ids = []
    for review in reviews_data:
        review_content = f"{review['date']}_{review['name']}_{review['text']}"
        review_id = f"yandex_{company_id}_{hashlib.md5(review_content.encode()).hexdigest()[:10]}"
        if review_id in existing_ids:
            continue
        if review['date']:
            timestamp_ms = (review['date'] + 4 * 3600) * 1000
        else:
            timestamp_ms = int(time.time() * 1000)
        new_reviews.append({
            "review_id": review_id,
            "address": f"Яндекс ID: {company_id}",
            "date": timestamp_ms,
            "author": review['name'],
            "rating": review['stars'],
            "text": review['text'] or "Нет текста",
            "answer": review.get('answer', '')
        })
        new_ids.append(review_id)
    return new_reviews, new_ids

async def check_2gis_reviews():
    print("Начинаем проверку 2GIS отзывов...")
    existing_ids = db.get_existing_ids("2gis")
    all_new_reviews = []
    all_updated_reviews = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_reviews(session, link, existing_ids) for link in BRANCH_URLS if link.strip()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                print(f"Ошибка при сборе отзывов 2GIS: {result}")
                continue
            new_reviews, updated_reviews = result
            all_new_reviews.extend(new_reviews)
            all_updated_reviews.extend(updated_reviews)
    if all_new_reviews:
        print(f"2GIS: Найдено новых отзывов: {len(all_new_reviews)}")
    if all_updated_reviews:
        print(f"2GIS: Найдено обновленных отзывов: {len(all_updated_reviews)}")
    return len(all_new_reviews) > 0 or len(all_updated_reviews) > 0

async def check_yandex_reviews():
    print("Начинаем проверку Яндекс отзывов...")
    existing_ids = db.get_existing_ids("yandex")
    all_new_reviews = []
    all_new_ids = []
    for company_id in YANDEX_IDS:
        reviews, ids = await asyncio.to_thread(parse_yandex_reviews, company_id, existing_ids)
        all_new_reviews.extend(reviews)
        all_new_ids.extend(ids)
    if all_new_reviews:
        for review in all_new_reviews:
            db.save_review("yandex", review)
        print(f"Яндекс: Найдено новых отзывов: {len(all_new_reviews)}")
        return True
    else:
        print("Яндекс: Новых отзывов не найдено")
        return False

async def send_unsent_reviews():
    print("Проверяем неотправленные отзывы...")
    unsent_reviews = db.get_unsent_reviews("2gis")
    print(f"Найдено {len(unsent_reviews)} неотправленных отзывов из 2GIS")
    for review in unsent_reviews:
        review_text = format_review(review, "2GIS")
        success = await send_message(review_text)
        if success:
            db.mark_as_sent("2gis", review['review_id'])
        await asyncio.sleep(3)
    unsent_reviews = db.get_unsent_reviews("yandex")
    print(f"Найдено {len(unsent_reviews)} неотправленных отзывов из Яндекс")
    for review in unsent_reviews:
        review_text = format_review(review, "Yandex")
        success = await send_message(review_text)
        if success:
            db.mark_as_sent("yandex", review['review_id'])
        await asyncio.sleep(3)

async def main():
    print("Запуск бота для мониторинга отзывов (MAX)...")
    migrate_database()
    print("Первоначальная проверка отзывов...")
    try:
        await check_2gis_reviews()
        await check_yandex_reviews()
        await send_unsent_reviews()
    except Exception as e:
        print(f"Ошибка при первоначальной проверке: {e}")
    while True:
        try:
            print("Ожидание следующей проверки (5 минут)...")
            await asyncio.sleep(300)
            print("Начинаем очередную проверку отзывов...")
            await check_2gis_reviews()
            await check_yandex_reviews()
            await send_unsent_reviews()
            print("Проверка завершена успешно.")
        except Exception as e:
            print(f"Ошибка в основном цикле: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
    except Exception as e:
        print(f"Критическая ошибка: {e}")