from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time

opts = Options()
opts.add_argument('--headless')
opts.add_argument('--no-sandbox')
driver = webdriver.Chrome(options=opts)

driver.get('http://localhost:5000/')
driver.execute_script("sessionStorage.setItem('lastSearchTranscript', 'test'); sessionStorage.setItem('lastSearchResults', '{\"schedules\": [{\"transport_type\": \"bus\", \"departure_time\": \"10:00\", \"route_id\": 1, \"available_seats\": 10, \"schedule_id\": 1}], \"origin\": \"A\", \"destination\": \"B\"}');")
driver.refresh()
time.sleep(1)
print("Transcript:", driver.execute_script("return document.getElementById('speech-transcript').textContent;"))
print("Results:", driver.execute_script("return document.getElementById('results-output').innerHTML;"))
driver.quit()
