from flask import Flask, render_template, request,make_response,send_from_directory,Response,jsonify
import asyncio
import aiohttp
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import csv
import io
import requests
from openai import OpenAI
import json
import datetime
import random
import os
import chardet
from dotenv import load_dotenv

import ssl
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

load_dotenv()
app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route('/check-meta-data', methods=['POST'])
async def check_meta_data():
    siteurl = request.json.get('siteurl')
    sitemap_url = get_sitemap_url(siteurl)

    async def generate():
        async with aiohttp.ClientSession() as session:
            urls = await fetch_urls_from_sitemap(session, sitemap_url)
            if len(urls) == 0:
                yield json.dumps({'error': 'Sitemap does not exist'})
            else:
                for url in urls:
                    info = await extract_meta_info(session, url)
                    yield json.dumps(info)
                    yield '\n\n'

    async def generate_wrapped():
        async for chunk in generate():
            yield chunk

    return Response(generate_wrapped(), content_type='text/event-stream')

def get_sitemap_url(siteurl):
    modified_url = siteurl.strip()
    if not modified_url.endswith('/'):
        modified_url += '/'
    modified_url += 'sitemap.xml'
    return modified_url
@app.route('/download-csv', methods=['POST'])
def download_csv():
    siteurl = request.form['siteurl']
    meta_data = get_meta_info_sync(siteurl)
    
    csv_buffer = io.StringIO()
    
    csv_writer = csv.writer(csv_buffer)
    csv_writer.writerow(['URL', 'Title', 'Description', 'Image'])
    
    base_url = get_base_url(siteurl)
    
    for url, title, description, image in meta_data:
        if image != 'N/A' and not image.startswith('http') :
            image = urljoin(base_url, image)
        csv_writer.writerow([url, title, description, image])
    
    csv_data = csv_buffer.getvalue()
    
    response = make_response(csv_data)
    filename = f"{siteurl}_meta_data.csv"
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Content-Type'] = 'text/csv'
    
    return response


async def extract_meta_info(session, url):
    async with session.get(url,ssl=ssl_context) as response:
        content_type = response.headers.get('content-type', '')
        encoding = None
        
        soup = BeautifulSoup(await response.text(), 'html.parser')
        meta_charset = soup.find('meta', attrs={'charset': True})
        if meta_charset:
            encoding = meta_charset['charset']

        if 'charset=' in content_type:
            encoding = content_type.split('charset=')[-1]
        
        if encoding is None:
            content = await response.read()
            encoding = chardet.detect(content)['encoding']

        response_text = await response.text(encoding=encoding)
        print(encoding)
        soup = BeautifulSoup(response_text, 'html.parser')

        title_tag = soup.find('meta', property='og:title')
        title = title_tag['content'] if title_tag else 'N/A'
        description_tag = soup.find('meta', property='og:description')
        description = description_tag['content'] if description_tag else 'N/A'
        image_tag = soup.find('meta', property='og:image')
        image = image_tag['content'] if image_tag else 'N/A'
        return url, title, description, image

def get_base_url(url):
    parsed_url = urlparse(url)
    return f"{parsed_url.scheme}://{parsed_url.netloc}"

@app.template_filter()
def attach_base_url(url, siteurl):
    if not url.startswith("http"):
        base_url = get_base_url(siteurl)
        url = urljoin(base_url, url)
    return url

async def fetch_urls_from_sitemap(session, url):
    main_sitemap_url = url
    async with session.get(url,ssl=ssl_context) as response:
        soup = BeautifulSoup(await response.text(), 'html.parser')
        urls = [loc.text.strip() for loc in soup.find_all('loc')]

        nested_sitemaps = [u for u in urls if u.endswith('.xml') or u.endswith('.xml.gz')]
        if main_sitemap_url:
            nested_sitemaps = [u for u in nested_sitemaps if u != main_sitemap_url]
        if nested_sitemaps: 
            nested_urls = await asyncio.gather(*[fetch_urls_from_sitemap(session, nested_url) for nested_url in nested_sitemaps])
            for nested_url_list in nested_urls:
                urls.extend(nested_url_list)
        urls = [u for u in urls if not u.endswith('.xml') and not u.endswith('.xml.gz')]
        return urls

async def process_urls(session, urls):
    tasks = []
    for url in urls:
        task = asyncio.ensure_future(extract_meta_info(session, url))
        tasks.append(task)
    results = await asyncio.gather(*tasks)
    return results

async def get_meta_info(url):
    try:
        async with aiohttp.ClientSession() as session:
            urls = await fetch_urls_from_sitemap(session, url)
            if(len(urls) == 0):
                return "Sitemap is not available, you can provide a custom URL for the sitemap. Simply enter the custom URL to proceed."
            meta_info = await process_urls(session, urls)
            return meta_info
    except aiohttp.client_exceptions.ServerDisconnectedError:
        await asyncio.sleep(3)
        return await get_meta_info(url)

# async def get_meta_info_with_timeout(url):
#     try:
#         return await asyncio.wait_for(get_meta_info(url), timeout=30)
#     except asyncio.TimeoutError:
#         return "Sitemap is too large. We are working to make this work"

def get_meta_info_sync(url):
    return asyncio.run(get_meta_info(url))

@app.route('/robots.txt')
def serve_robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt')

@app.route('/sitemap.xml')
def serve_sitemap():
    return send_from_directory(app.static_folder, 'sitemap.xml')

@app.route('/ai-call', methods=['POST'])
def openAI():
    parsed_results = {}
    if request.method == 'POST':
        data = request.json
        website_link = data.get('pageUrl')
        print(website_link)
        if website_link:
            try:
                url_response = requests.get(website_link)
                url_response.raise_for_status()
                url_html_content = url_response.text

                soup = BeautifulSoup(url_html_content, 'html.parser')
                body = soup.find('body')
                if body:
                    for script in body.find_all('script'):
                        script.extract()
                    for style in body.find_all('style'):
                        style.extract()
                    body_content = str(body)
                    cut_words = 7000
                    if len(body_content) > 7000:
                        body_content = body_content[:cut_words]

                    prompt = f'Within the body code of a website, you will find the following content:\n\n{body_content}\n\nPlease analyze this content and identify its tone. Based on the tone, suggest suitable meta title, meta description tags, and an complete SEO schema. Ensure that the generated meta tags adhere to best practices for character limits. Be sure to mention the tone you have identified in your response. Provide the response in JSON format. Always provide response by strictly following this format {{"tone": tone you idetified,"metaTitle":Meta Title you are suggesting,"metaDescription":Meta Description you are suggesting,"seoSchema": Schema}}'

                    response = client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0
                    )
                    print(response)
                    if response and response.choices and response.choices[0].message.content:
                        inDictJson = json.loads(response.choices[0].message.content.strip())
                        print(inDictJson)
                        if "tone" in inDictJson and "metaTitle" in inDictJson and "metaDescription" in inDictJson and "seoSchema" in inDictJson:
                            parsed_results[website_link] = inDictJson
                        else:
                            parsed_results[website_link] = {"raw_response": response.choices[0].message.content.strip()}
                    else:
                        parsed_results[website_link] = {"raw_response": "No response or choices"}

            except Exception as e:
                error_message = f'Error parsing website: {str(e)}'
                return jsonify({'success': False, 'message': error_message})
        else:
            return jsonify({'success': False, 'message': 'Invalid request, pageUrl missing'})
    else:
        return jsonify({'success': False, 'message': 'Unsupported request method'})

    return jsonify({'success': True, 'parsed_results': parsed_results})


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        siteurl = request.form['siteurl']
        if siteurl.endswith('.xml'):
            sitemap_url = siteurl
        else:
            sitemap_url = get_sitemap_url(siteurl)
        meta_data = get_meta_info_sync((sitemap_url))
        missing_data_site_count = sum(1 for info in meta_data if all(data == 'N/A' or data == 'Not Found' for data in info[1:]))
        if isinstance(meta_data, str):
            meta_info = meta_data
            check = ('String')
        else:
            print("Into list")
        
            meta_info = meta_data
            check = ('List')
        return render_template('index.html', meta_info=meta_info,siteurl = siteurl,missing_data = missing_data_site_count,check=check)
    return render_template('index.html', meta_info='',check = '')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=4444)
