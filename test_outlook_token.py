from tools import OutlookClient

try:
    c = OutlookClient()
    print('Got client, base_url=', c.base_url)
    token = c._get_token()
    print('token present:', bool(token))
except Exception as e:
    print('Error creating client:', e)
    raise
