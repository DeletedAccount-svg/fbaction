import requests
import os
import json
from datetime import datetime

PAGE_ID = os.environ["FB_PAGE_ID"]
ACCESS_TOKEN = os.environ["FB_ACCESS_TOKEN"]

def get_todays_blog():
    # Ugh! Load blog list from file, pick today one
    with open("blogs.json", "r") as f:
        blogs = json.load(f)
    
    # Pick blog by day number (rotate through list)
    day_of_year = datetime.now().timetuple().tm_yday
    blog = blogs[day_of_year % len(blogs)]
    return blog

def post_to_facebook(message, link=None):
    url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/feed"
    
    payload = {
        "message": message,
        "access_token": ACCESS_TOKEN
    }
    if link:
        payload["link"] = link  # Blog URL make nice preview!
    
    response = requests.post(url, data=payload)
    result = response.json()
    
    if "id" in result:
        print(f"UGH! Post success! ID: {result['id']}")
    else:
        print(f"OOF! Error: {result}")
        raise Exception("Post fail!")

if __name__ == "__main__":
    blog = get_todays_blog()
    post_to_facebook(
        message=blog["caption"],
        link=blog["url"]
    )
