import requests, re

s = requests.Session()
r = s.get('https://rabasoff.pythonanywhere.com/login')
csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
s.post('https://rabasoff.pythonanywhere.com/login', 
    data={'username':'admin','password':'admin123','csrf_token':csrf},
    headers={'Referer': 'https://rabasoff.pythonanywhere.com/login'})

# Check Mule page content in detail
r = s.get('https://rabasoff.pythonanywhere.com/mule')
content = r.text

print("=== MULE PAGE ANALYSIS ===")
print(f"Size: {len(content)} bytes")

# Check for specific content
if 'Mule Maintenance' in content:
    print("Title: FOUND")
else:
    print("Title: MISSING")

# Check for buttons
if 'New Record' in content:
    print("New Record button: FOUND")
else:
    print("New Record button: MISSING")

if 'Order Parts' in content:
    print("Order Parts button: FOUND")
else:
    print("Order Parts button: MISSING")

# Check for form fields
if 'mule_number' in content:
    print("mule_number field: FOUND")
else:
    print("mule_number field: MISSING")

# Check for stats
if 'Total Records' in content:
    print("Total Records: FOUND")
else:
    print("Total Records: MISSING")

# Check for table
if 'Maintenance Records' in content:
    print("Maintenance Records: FOUND")
else:
    print("Maintenance Records: MISSING")

# Check for filter
if 'Serial Number' in content:
    print("Serial Number filter: FOUND")
else:
    print("Serial Number filter: MISSING")

# Check for table rows
table_rows = content.count('<tr')
print(f"Table rows: {table_rows}")

# Check if it's showing the mule template or dashboard
if 'Dashboard' in content[:2000]:
    print("\nWARNING: Showing DASHBOARD instead of Mule page")
elif 'Mule Maintenance' in content:
    print("\nOK: Showing MULE page")

# Show first 2000 chars of body
body_start = content.find('<body')
if body_start > 0:
    print(f"\n=== BODY PREVIEW (first 2000 chars) ===")
    print(content[body_start:body_start+2000])
