from tools import OutlookClient

try:
    c = OutlookClient()
    resp = c.send_message(subject='Test from LiveKit Jarvis', body='This is a test email sent by automated test.', to_recipients=[c.user])
    print('send_message response:', resp)
except Exception as e:
    print('Error sending message:', e)
    raise
