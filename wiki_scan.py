"""Scan wiki structure to verify all game content is captured."""
import requests, json

# Fetch ALL categories
params = {
    'action': 'query', 'list': 'allcategories',
    'aclimit': 500, 'acprop': 'size', 'format': 'json'
}
r = requests.get('https://gray-zone-warfare.fandom.com/api.php',
                 params=params, timeout=30)
cats = r.json().get('query', {}).get('allcategories', [])

# Categories we KNOW are handled via listing_pages (not page-based)
LISTING_HANDLED = {'Loot', 'Apparel', 'Provisions'}

# Categories we explicitly skip
SKIP_NAMES = {
    'Images needing improvement', 'Pages with broken file links', 'Newspaper',
    'Template documentation', 'Documentation templates', 'Templates',
    'Notice templates', 'Evidence', 'Image license templates', 'Regions',
    'General wiki templates', 'Infobox templates', 'Formatting templates',
    'Factions', 'Maintenance', 'Wiki maintenance', 'Auxiliary templates',
    'Community', 'Maps', 'Citation needed', 'Verification needed',
    'Archive', 'Pages missing details', 'Candidates for deletion',
    'Removed Content', 'Upcoming Content', 'Gray Zone Warfare Wiki',
    'Pages missing details', 'Candidates for deletion',
    'Images', 'Image', 'Videos', 'Video', 'Audio', 'Audio files',
    'Templates', 'Template', 'Users', 'User', 'Files', 'File',
    'Categories', 'Category', 'Help', 'Stubs', 'Disambiguation',
    'Redlinks', 'Broken redirects', 'Articles', 'Pages with',
    'Real world', 'Staff', 'Administration', 'Screenshots', 'Concept art',
    'Gameplay', 'Multiplayer', 'Pages with broken file links',
    'Need images', 'Pages with missing images', 'Pages with unavailable images',
    'Infobox templates', 'Navigation templates', 'Featured articles',
    'Good articles', 'Pages missing details', 'Pages needing',
    'Protected pages', 'Blog posts', 'Blog listing', 'Blog feed',
    'Front page', 'Basics', 'Characters', 'Locations', 'Media',
    'Maintenance', 'Your locker', 'Navbox templates',
    'Section formatting templates', 'Formatting templates',
    'General wiki templates', 'Auxiliary templates', 'Design template',
    'Quote templates', 'Link Template', 'Noindexed pages',
    'Wiki skin images', 'Wiki maintenance', 'Front page sections',
    'Pages using duplicate arguments in template calls',
    'Image and media templates', 'Candidates for deletion',
}

print("=== CATEGORIES NOT OBVIOUSLY HANDLED ===")
print("(game-relevant categories that might need attention)\n")

for c in cats:
    name = c.get('*', '')
    title = name.replace('_', ' ')
    pages = c.get('size', 0)

    # Skip empty
    if pages == 0:
        continue
    
    # Skip known infrastructure
    if title in SKIP_NAMES:
        continue
    if any(name.startswith(p) for p in ['T_', 'P_', 'F_', 'I_', 'U_', 'H_']):
        continue
    
    # Skip listing-page-handled
    if title in LISTING_HANDLED:
        continue
    
    # Skip ammo subcategories (all mapped to ammo.json)
    if 'ammunition' in title.lower() or 'ammo' in title.lower():
        continue
    if '4.6x30mm' in title:
        continue
    if any(title.startswith(p) for p in ['.2', '.3', '.4', '.S', '12-', '5.', '7.', '9x']):
        continue
    
    # Show everything else that has pages
    print(f"  {title}: {pages} pages")

print(f"\nTotal wiki categories: {len(cats)}")
print(f"Skip list size: {len(SKIP_NAMES)}")
