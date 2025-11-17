from tools import OutlookClient

try:
    c = OutlookClient()
    subject = "Test debug send"
    body = "This is a debug send from automated test."
    resp = c.send_message(subject=subject, body=body, to_recipients=["biyela.mj@gmail.com"])    
    print("send_message response:", resp)
    url = f"{c.base_url}/mailFolders/SentItems/messages?$filter=startsWith(subject,'{subject}')"
    r = c._session.get(url)
    print("SentItems status", r.status_code)
    vals = r.json().get('value', [])
    print('SentItems results (first 5):', vals[:5])
except Exception as e:
    print('Error:', e)
    raise
