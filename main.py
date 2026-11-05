import asyncio
import aiohttp
import csv
import os
import html
import time
import re
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Конфигурация (остается без изменений)
REVIEW_ID_FILE = "review_ids.txt"
SENT_IDS_FILE = "sent_ids.txt"
CSV_FILE = "reviews.csv"
YANDEX_REVIEW_ID_FILE = "yandex_review_ids.txt"
YANDEX_SENT_IDS_FILE = "yandex_sent_ids.txt"
YANDEX_CSV_FILE = "yandex_reviews.csv"

API_KEY = "6e7e1929-4ea9-4a5d-8c05-d601860389bd" 
BRANCH_URLS = [""]
YANDEX_IDS = [1654386580]
API_TOKEN = "9013815502:AAEs_kGOJWssf8i9sz4tYlxh2lsSbKhO42I" # add api_token 5.11.26
CHANNEL_ID = -1003980122727 # add chanel id 5.11.26

# Инициализация бота
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

# Классы для парсинга Яндекс (с изменениями для уменьшения ошибок)
class ParserHelper:
    @staticmethod
    def get_count_star(stars_elements):
        count = 0
        for star in stars_elements:
            classes = star.get_attribute('class')
            if '._full' in classes or '_full' in classes:
                count += 1
            elif '._half' in classes or '_half' in classes:
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
        self.driver.execute_script(
            "arguments[0].scrollIntoView();",
            elem
        )
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

        return {
            'name': name,
            'rating': rating,
            'count_rating': count_rating,
            'stars': stars_count
        }

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
        return {
            'company_info': self.__get_data_company(),
            'company_reviews': self.__get_data_reviews()
        }

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
        #chrome_options.add_argument('--headless')
        chrome_options.add_argument('--log-level=3')  # Уменьшаем уровень логов
        chrome_options.add_argument('--disable-logging')  # Отключаем логи
        
        # Добавляем опции для отключения сообщений DevTools
        chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
        chrome_options.add_argument("--disable-dev-tools")  # Отключаем DevTools
        
        # Отключаем различные службы и функции
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

# Общие функции для бота
async def send_message(text: str):
    try:
        await bot.send_message(CHANNEL_ID, text)
        return True
    except Exception as e:
        if "Flood control exceeded" in str(e):
            try:
                retry_after = int(str(e).split("retry after ")[1].split(" ")[0])
                print(f"Обнаружено ограничение частоты. Ждем {retry_after} секунд...")
                await asyncio.sleep(retry_after + 2)
                await bot.send_message(CHANNEL_ID, text)
                return True
            except (IndexError, ValueError):
                print(f"Не удалось извлечь время ожидания: {e}")
                return False
        else:
            print(f"Ошибка отправки сообщения: {e}")
            return False

def load_existing_ids(filename):
    if not os.path.exists(filename):
        return set()
    with open(filename, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f)

def save_ids(filename, new_ids):
    with open(filename, "a", encoding="utf-8") as f:
        for rid in new_ids:
            f.write(rid + "\n")

def save_reviews_to_csv(reviews, csv_file):
    write_header = not os.path.exists(csv_file)
    # Добавляем поля для ответа компании и даты ответа
    fieldnames = ["address", "date", "author", "rating", "text", "answer", "answer_date"] if "yandex" not in csv_file else ["address", "date", "author", "rating", "text", "answer"]
    
    with open(csv_file, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        if write_header:
            writer.writeheader()
        writer.writerows(reviews)

def format_datetime(dt_value):
    """Форматирует дату-время из timestamp или строки в ЧЧ:ММ:СС-ДД.ММ.ГГГГ"""
    try:
        if isinstance(dt_value, (int, float)):
            dt = datetime.fromtimestamp(dt_value, timezone.utc)
        else:
            # Обрабатываем разные форматы дат
            if 'T' in dt_value:
                if '+' in dt_value:
                    dt = datetime.fromisoformat(dt_value.replace('Z', '+00:00'))
                else:
                    dt = datetime.fromisoformat(dt_value.replace('Z', ''))
            else:
                dt = datetime.strptime(dt_value, '%Y-%m-%d %H:%M:%S')
        dt = dt.astimezone(timezone(timedelta(hours=7)))
        return dt.strftime("%H:%M:%S %d.%m.%Y")
    except (ValueError, AttributeError):
        return str(dt_value)

def format_review(review, source="2GIS"):
    escaped_text = html.escape(review['text'])
    if len(escaped_text) > 4000:
        escaped_text = escaped_text[:4000] + "..."
    
    review_date = format_datetime(review['date'])
    tz = timezone(timedelta(hours=7))
    current_time = datetime.now(tz).strftime("%H:%M:%S %d.%m.%Y")
    
    if source == "2GIS":
        message = (f"<b>📝 Отзыв с 2ГИС</b>\n\n"
                  f"<b>🏢 Адрес:</b> {html.escape(review['address'])}\n"
                  f"<b>📅 Дата отзыва:</b> {review_date}\n"
                  f"<b>👤 Автор:</b> {html.escape(review['author'])}\n"
                  f"<b>⭐ Рейтинг:</b> {review['rating']}/5\n\n"
                  f"<b>💬 Текст:</b>\n{escaped_text}\n\n")
        
        # Добавляем ответ компании, если он есть
        if review.get('answer'):
            escaped_answer = html.escape(review['answer'])
            if len(escaped_answer) > 4000:
                escaped_answer = escaped_answer[:4000] + "..."
            
            answer_date = format_datetime(review.get('answer_date', '')) if review.get('answer_date') else "не указана"
            message += (f"<b>📣 Ответ компании:</b>\n"
                       f"{escaped_answer}\n"
                       f"<b>📅 Дата ответа:</b> {answer_date}\n\n")
        
        #message += f"<i>🕐 Проверено: {current_time}</i>"
        
        return message
    else:
        # Форматирование для Яндекс с ответом компании
        message = (f"<b>📝 Отзыв с Яндекс Карт</b>\n\n"
                  f"<b>🏢 Организация:</b> {html.escape(review['address'])}\n"
                  f"<b>📅 Дата отзыва:</b> {review_date}\n"
                  f"<b>👤 Автор:</b> {html.escape(review['author'])}\n"
                  f"<b>⭐ Рейтинг:</b> {review['rating']}/5\n\n"
                  f"<b>💬 Текст:</b>\n{escaped_text}\n\n")
        
        # Добавляем ответ компании, если он есть
        if review.get('answer'):
            escaped_answer = html.escape(review['answer'])
            if len(escaped_answer) > 4000:
                escaped_answer = escaped_answer[:4000] + "..."
            message += f"<b>📣 Ответ компании:</b>\n{escaped_answer}\n\n"
        
        #message += f"<i>🕐 Проверено: {current_time}</i>"
        
        return message

# Функции для 2GIS
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
    new_ids = []
    limit = 50
    offset = 0

    while True:
        api_url = f"https://public-api.reviews.2gis.com/2.0/branches/{firm_id}/reviews"
        params = {
            "limit": limit,
            "offset": offset,
            "is_advertiser": "false",
            "fields": "meta.providers,meta.branch_rating,meta.branch_reviews_count,meta.total_count,reviews.hiding_reason,reviews.is_verified,reviews.emojis",
            "without_my_first_review": "false",
            "rated": "true",
            "sort_by": "date_edited",
            "key": API_KEY,
            "locale": "ru_RU"
        }

        try:
            async with session.get(api_url, params=params) as resp:
                data = await resp.json()
                reviews = data.get("reviews", [])
                if not reviews:
                    break

                for review in reviews:
                    rid = review["id"]
                    if rid in existing_ids:
                        continue

                    # Добавляем обработку ответа компании
                    official_answer = review.get("official_answer")
                    answer_text = ""
                    answer_date = ""
                    
                    if official_answer:
                        answer_text = official_answer.get("text", "")
                        answer_date = official_answer.get("date_created", "")

                    new_reviews.append({
                        "address": address,
                        "date": review.get("date_edited") or review.get("date_created"),
                        "author": review["user"]["name"],
                        "rating": review.get("rating", ""),
                        "text": review.get("text", "").replace("\n", " ").strip(),
                        "answer": answer_text,
                        "answer_date": answer_date
                    })
                    new_ids.append(rid)

                if len(reviews) < limit:
                    break
                    
                offset += limit
                await asyncio.sleep(1)
        except Exception as e:
            print(f"Ошибка при запросе {url}: {e}")
            break

    # Сортируем отзывы от старых к новым
    if new_reviews:
        try:
            # Создаем временный список с датами для сортировки
            sorted_reviews = sorted(
                new_reviews,
                key=lambda x: x['date'] if x['date'] else '0'
            )
            # Сохраняем порядок ID в соответствии с отсортированными отзывами
            sorted_ids = []
            for review in sorted_reviews:
                index = new_reviews.index(review)
                sorted_ids.append(new_ids[index])
            
            return sorted_reviews, sorted_ids
        except Exception as e:
            print(f"Ошибка при сортировке отзывов: {e}")
            return new_reviews, new_ids
    
    return new_reviews, new_ids

# Функции для Яндекс
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
        # Создаем уникальный ID на основе содержимого отзыва
        review_id = f"yandex_{company_id}_{review['date']}_{review['name']}"
        if review_id in existing_ids:
            continue
            
        new_reviews.append({
            "address": f"Яндекс ID: {company_id}",
            "date": review['date'],
            "author": review['name'],
            "rating": review['stars'],
            "text": review['text'] or "Нет текста",
            "answer": review.get('answer', '')  # Добавляем ответ компании
        })
        new_ids.append(review_id)
        
    return new_reviews, new_ids

async def check_2gis_reviews():
    existing_ids = load_existing_ids(REVIEW_ID_FILE)
    sent_ids = load_existing_ids(SENT_IDS_FILE)
    
    all_new_reviews = []
    all_new_ids = []

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_reviews(session, link, existing_ids) for link in BRANCH_URLS if link.strip()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                print(f"Ошибка при сборе отзывов 2GIS: {result}")
                continue
            reviews, ids = result
            all_new_reviews.extend(reviews)
            all_new_ids.extend(ids)

    if all_new_reviews:
        save_reviews_to_csv(all_new_reviews, CSV_FILE)
        save_ids(REVIEW_ID_FILE, all_new_ids)
        
        for review in all_new_reviews:
            review_text = format_review(review, "2GIS")
            success = await send_message(review_text)
            if success:
                review_id = all_new_ids[all_new_reviews.index(review)]
                save_ids(SENT_IDS_FILE, [review_id])
            await asyncio.sleep(3)
        
        print(f"2GIS: Найдено и отправлено новых отзывов: {len(all_new_reviews)}")
        return True
    else:
        print("2GIS: Новых отзывов не найдено")
        return False


async def check_yandex_reviews():
    existing_ids = load_existing_ids(YANDEX_REVIEW_ID_FILE)
    sent_ids = load_existing_ids(YANDEX_SENT_IDS_FILE)
    
    all_new_reviews = []
    all_new_ids = []

    for company_id in YANDEX_IDS:
        reviews, ids = await asyncio.to_thread(parse_yandex_reviews, company_id, existing_ids)
        all_new_reviews.extend(reviews)
        all_new_ids.extend(ids)

    if all_new_reviews:
        save_reviews_to_csv(all_new_reviews, YANDEX_CSV_FILE)
        save_ids(YANDEX_REVIEW_ID_FILE, all_new_ids)
        
        for review in all_new_reviews:
            review_text = format_review(review, "Yandex")
            success = await send_message(review_text)
            if success:
                review_id = all_new_ids[all_new_reviews.index(review)]
                save_ids(YANDEX_SENT_IDS_FILE, [review_id])
            await asyncio.sleep(3)
        
        print(f"Яндекс: Найдено и отправлено новых отзывов: {len(all_new_reviews)}")
        return True
    else:
        print("Яндекс: Новых отзывов не найдено")
        return False

async def main():
    print("Запуск бота для мониторинга отзывов...")
    print("Первоначальная проверка отзывов...")
    
    await check_2gis_reviews()
    await check_yandex_reviews()
    
    while True:
        print("Ожидание следующей проверки (5 минут)...")
        await asyncio.sleep(5 * 60)
        
        print("Начинаем очередную проверку отзывов...")
        await check_2gis_reviews()
        await check_yandex_reviews()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")