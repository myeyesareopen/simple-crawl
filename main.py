import aiohttp
import html2text
from bs4 import BeautifulSoup
import time
import random
import urllib.robotparser
from urllib.parse import urlparse, urlunparse
import re
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
import aioredis
import hashlib

app = FastAPI()

user_agents = [
    "Omniracle.com / Bot 1.0"
]

rds = None

async def connect_to_redis(host='splash-redis', port=6379, db=0):
    redis_url = f"redis://{host}:{port}/{db}"
    redis = await aioredis.from_url(redis_url)
    return redis

async def is_allowed_by_robots_txt_content(robots_txt_content, target_url, user_agent='OmniracleBot'):
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_txt_content.splitlines())
    return rp.can_fetch(user_agent, target_url)

async def is_allowed_by_robots(request_url, proxy=None):
    global rds
    if rds == None:
        rds = await connect_to_redis()
    parsed_url = urlparse(request_url)
    # 构建robots.txt的URL
    robots_url = urlunparse((parsed_url.scheme, parsed_url.netloc, '/robots.txt', '', '', ''))
    domain = parsed_url.netloc

    if await rds.exists('robots_' + domain):
        robots_txt_content = await rds.get('robots_' + domain)
    else:
        if proxy:
            proxy = proxy
        else:
            proxy = "http://192.168.1.2:10809"

        async with aiohttp.ClientSession() as session:
            async with session.get(robots_url, proxy=proxy) as resp:
                if resp.status == 200:
                    robots_txt_content = await resp.text()
                else:
                    robots_txt_content = ''

        await rds.set('robots_' + domain, robots_txt_content, ex=86400*7)

    if isinstance(robots_txt_content, bytes):
        robots_txt_content = robots_txt_content.decode('utf-8')

    if await is_allowed_by_robots_txt_content(robots_txt_content, request_url):
        return True
    else:
        return False

def string_to_md5(input_string):
    md5_hash = hashlib.md5()
    md5_hash.update(input_string.encode('utf-8'))
    return md5_hash.hexdigest()

async def render_html_with_splash(url, user_agent, proxy=None):
    splash_url = 'http://splash:8050/render.html'
    headers = {
        'User-Agent': user_agent,
    }
    params = {
        'url': url,
        'wait': 1,  # 等待2秒以确保页面完全加载
        'timeout': 90,  # 超时时间设置为90秒
    }
    if proxy:
        params['proxy'] = proxy
    else:
        params['proxy'] = "http://192.168.1.2:10809"

    async with aiohttp.ClientSession() as session:
        async with session.get(splash_url, params=params, headers=headers, proxy=proxy) as response:
            if response.status == 200:
                return await response.text()
            else:
                raise Exception(f"Failed to render page: {response.status}")

async def remove_js_css(html):
    soup = BeautifulSoup(html, 'html.parser')

    # 删除所有 <script> 标签
    for script in soup(["script", "style"]):
        script.decompose()

    return str(soup)

async def extract_metadata(html):
    soup = BeautifulSoup(html, 'html.parser')

    # 提取 <title>
    title = soup.title.string if soup.title else 'No title found'

    # 提取 <meta name="keywords">
    keywords = ''
    keywords_tag = soup.find('meta', attrs={'name': 'keywords'})
    if keywords_tag and 'content' in keywords_tag.attrs:
        keywords = keywords_tag['content']

    # 提取 <meta name="description">
    description = ''
    description_tag = soup.find('meta', attrs={'name': 'description'})
    if description_tag and 'content' in description_tag.attrs:
        description = description_tag['content']

    result = {
        'title': title,
        'keywords': keywords,
        'description': description,
    }

    # 提取所有有 property 属性的 <meta> 标签
    meta_properties = {}
    for meta_tag in soup.find_all('meta', attrs={'property': True}):
        property_name = meta_tag['property']
        content = meta_tag.get('content', '')
        result[property_name] = content

    return result

async def convert_html_to_text(html):
    h = html2text.HTML2Text()
    h.ignore_links = True  # 忽略链接
    h.ignore_images = True  # 忽略图片
    h.ignore_emphasis = True  # 忽略强调
    h.ignore_tables = False  # 不忽略表格
    h.body_width = 99999990  # 每行字符数限制为80
    h.single_line_break = False  # 双换行转换为单换行
    h.unicode_snob = True  # 保留Unicode字符
    h.wrap_links = False  # 不在链接两端添加尖括号
    h.inline_links = False  # 不将链接转换为内联格式
    h.bypass_tables = False  # 不直接返回表格的HTML代码
    return h.handle(html)

async def filter_lines_by_word_count(text, min_words):
    # 分割文本为行，并过滤掉单词数量小于 min_words 的行
    filtered_lines = [line for line in text.splitlines() if len(line.split()) >= min_words]
    return "\n".join(filtered_lines)

async def extract_media_urls(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')

    image_list = []
    images = soup.find_all('img')
    for img in images:
        img_src = img.get('src')
        img_alt = img.get('alt')
        image_list.append({"src": img_src, "alt": img_alt})

    video_list = []
    videos = soup.find_all('video')
    for video in videos:
        video_src = video.get('src')
        if not video_src:
            source = video.find('source')
            video_src = source.get('src') if source else None
        video_list.append({"src": video_src})

    audio_list = []
    audios = soup.find_all('audio')
    for audio in audios:
        audio_src = audio.get('src')
        if not audio_src:
            source = audio.find('source')
            audio_src = source.get('src') if source else None
            audio_list.append({"src": audio_src})
    return {"images": image_list, "videos": video_list, "audios": audio_list}

async def extract_links(html_content, domain):
    soup = BeautifulSoup(html_content, 'html.parser')

    links = []
    # 提取所有链接的文本和 URL
    for a in soup.find_all('a'):
        if a.get('href'):
            link_text = a.get_text(strip=True)
            href = a.get('href')

            if href == '#' or href == '/':
                href = f"https://{domain}"

            if re.match('/', href) and not re.match('//', href):
                href = f"https://{domain}{href}"

            if re.match('http', href):
                links.append({"text": link_text, "url": href})

    return links

class URLRequest(BaseModel):
    url: str
    proxy: str = None

@app.post("/crawl/")
async def crawl_url(request: URLRequest):
    global rds

    if await is_allowed_by_robots(request.url, request.proxy) == False:
        response = {
            "success": False,
            "url": request.url,
            "errorMessage": "Fobiden by robots.txt"
        }
        return response

    if rds == None:
        rds = connect_to_redis()

    try:
        start = time.time()
        url = request.url
        proxy = request.proxy
        url_md5 = string_to_md5(url)
        if await rds.exists('crawl_' + url_md5) == 0:
            domain = urlparse(url).netloc
            user_agent = random.choice(user_agents)
            html_content = await render_html_with_splash(url, user_agent, proxy)
            cleaned_html = await remove_js_css(html_content)

            media = await extract_media_urls(cleaned_html)
            meta = await extract_metadata(cleaned_html)
            links = await extract_links(cleaned_html, domain)
            text_content = await convert_html_to_text(cleaned_html)

            min_words = 10
            filtered_text = await filter_lines_by_word_count(text_content, min_words)

            end = time.time()

            response = {
                "success": True,
                "url": url,
                "meta": meta,
                "content": filtered_text,
                "media": media,
                "links": links,
                "errorMessage": ""
            }

            await rds.set('crawl_' + url_md5, str(time.time()), ex=86400*7)

            print(f"[LOG] Crawled {url}")
            print(f"[LOG] time cost: {round(end-start, 3)} seconds")
        else:
            response = {
                "success": False,
                "errorMessage": f"url: {url} md5: {url_md5} is freezing."
            }
    except Exception as e:
        end = time.time()
        response = {
            "success": False,
            "url": url,
            "errorMessage": str(e)
        }

        print(f"[LOG] Something wrong when crawling {url}")
        print(f"[LOG] time cost: {round(end-start, 3)} seconds")

    print(f"[LOG] Response: {response}")

    return response

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
~                                                                             
