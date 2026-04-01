import requests

url = "https://dn70lgttucvdk.cloudfront.net/96/962e94347120b5efe6fd839e73b37bb34dba5b73.mp4"

headers = {
    "accept": "*/*",
    "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "priority": "i",
    "range": "bytes=0-",
    "referer": "https://dn70lgttucvdk.cloudfront.net/96/962e94347120b5efe6fd839e73b37bb34dba5b73.mp4",
    "sec-fetch-dest": "video",
    "sec-fetch-mode": "no-cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
}

print("Downloading...")
response = requests.get(url, headers=headers, stream=True)

if response.status_code in [200, 206]:
    with open("recordings/lecture1.mp4", "wb") as f:
        for chunk in response.iter_content(chunk_size=1024*1024):
            if chunk:
                f.write(chunk)
    print("Download complete! File saved to recordings/lecture1.mp4")
else:
    print(f"Failed. Status code: {response.status_code}")