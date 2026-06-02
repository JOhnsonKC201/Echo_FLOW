"""Bundled proper nouns protected from the aggressive de-Title-Case pass.

The casing flattener (cleanup._polish_text) lowercases mid-sentence Title-Case
words that aren't known proper nouns. That stops Whisper/LLM "Every Word
Capitalized" output — but, left to itself, it would also lowercase a brand or
name the user hasn't taught yet ("We met Sarah" -> "we met sarah").

This list seeds the protected set with common, *unambiguous* proper nouns so
the common cases survive untaught. We deliberately EXCLUDE proper nouns that
double as ordinary lowercase words ("Mark"/mark, "Will"/will, "May"/may,
"Bill"/bill, "Rose"/rose, "Grace"/grace, "Reading"/reading) — those stay
flattened unless the user explicitly teaches them, which is the whole point of
the aggressive mode the user opted into.

All entries are matched case-insensitively (the protected set is lowercased).
"""
from __future__ import annotations

# Tech companies, products, languages, tools — the vocabulary of a developer
# who dictates into Code/terminal most of the day.
_TECH = {
    "Google", "Microsoft", "Apple", "Amazon", "Facebook", "Meta", "Netflix",
    "YouTube", "Instagram", "Twitter", "LinkedIn", "GitHub", "GitLab", "Reddit",
    "TikTok", "WhatsApp", "Snapchat", "Spotify", "Discord", "Slack", "Zoom",
    "Nvidia", "Intel", "Oracle", "Salesforce", "Adobe", "Tesla", "Samsung",
    "Sony", "Nintendo", "Uber", "Airbnb", "PayPal", "Stripe", "Shopify",
    "Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Swift", "Rust",
    "Golang", "Ruby", "Scala", "Haskell", "Perl", "Django", "Flask", "React",
    "Angular", "Vue", "Svelte", "Node", "Express", "Spring", "Laravel",
    "Linux", "Windows", "Android", "Ubuntu", "Debian", "Fedora", "Docker",
    "Kubernetes", "Postgres", "PostgreSQL", "MongoDB", "Redis", "SQLite",
    "Whisper", "Ollama", "Anthropic", "OpenAI", "ChatGPT", "Claude", "Gemini",
    "Nvidia", "AWS", "Azure", "Vercel", "Netlify", "Cloudflare", "Supabase",
}

# Countries and nationalities — unambiguous (no common-word collisions).
_PLACES_COUNTRY = {
    "America", "Canada", "Mexico", "Brazil", "Argentina", "Colombia", "Chile",
    "England", "Scotland", "Ireland", "France", "Germany", "Spain", "Italy",
    "Portugal", "Netherlands", "Belgium", "Switzerland", "Austria", "Sweden",
    "Norway", "Denmark", "Finland", "Poland", "Greece", "Russia", "Ukraine",
    "China", "Japan", "Korea", "Vietnam", "Thailand", "Indonesia", "Malaysia",
    "Philippines", "India", "Pakistan", "Bangladesh", "Nepal", "Australia",
    "Zealand", "Egypt", "Nigeria", "Kenya", "Ghana", "Ethiopia", "Morocco",
    "Israel", "Iran", "Iraq", "Turkey", "Arabia", "Emirates", "Qatar",
    "American", "Canadian", "British", "French", "German", "Spanish",
    "Italian", "Chinese", "Japanese", "Korean", "Indian", "Russian",
    "Brazilian", "Mexican", "Australian", "Nigerian", "Egyptian",
}

# Major cities — picked to avoid English-word collisions ("Nice", "Mobile",
# "Reading", "Bath", "Hull" intentionally omitted).
_PLACES_CITY = {
    "London", "Paris", "Berlin", "Madrid", "Rome", "Vienna", "Amsterdam",
    "Brussels", "Lisbon", "Athens", "Moscow", "Warsaw", "Prague", "Budapest",
    "Tokyo", "Osaka", "Beijing", "Shanghai", "Seoul", "Bangkok", "Jakarta",
    "Mumbai", "Delhi", "Bangalore", "Karachi", "Lagos", "Cairo", "Nairobi",
    "Sydney", "Melbourne", "Toronto", "Montreal", "Vancouver", "Chicago",
    "Boston", "Seattle", "Atlanta", "Houston", "Dallas", "Denver", "Miami",
    "Philadelphia", "Brooklyn", "Manhattan", "Vegas", "Francisco", "Angeles",
    "Diego", "Antonio", "Jose", "Phoenix", "Detroit", "Baltimore",
}

# Continents/regions and the unambiguous SECOND word of common multi-word
# place names ("New York" -> York, "San Diego" -> Diego, "Saudi Arabia" ->
# Arabia). The first word is often an ordinary word (New, San, South) that we
# can't protect, but the distinctive second word carries the proper noun.
_PLACES_REGION = {
    "Africa", "Europe", "Asia", "Antarctica", "Oceania",
    "York", "Orleans", "Jersey", "Hampshire", "Mexico", "Zealand",
    "Kong", "Aires", "Janeiro", "Paulo", "Lanka", "Arabia", "Korea",
    "Carolina", "Dakota", "Virginia", "Columbia", "Dorado",
}

# Common, unambiguous first names. Names that double as ordinary words
# (Mark, Will, Bill, May, June, April, Grace, Rose, Joy, Hope, Faith, Dawn,
# Summer, Crystal, Holly, Jasmine, Pearl, Mary's "merry"?) are EXCLUDED.
_NAMES = {
    "Michael", "Christopher", "Matthew", "Joshua", "Andrew", "Daniel",
    "Joseph", "Anthony", "William", "Brian", "Kevin", "Jason", "Jeffrey",
    "Nicholas", "Jonathan", "Justin", "Brandon", "Tyler", "Aaron", "Adam",
    "Nathan", "Zachary", "Patrick", "Jacob", "Ethan", "Ryan", "Benjamin",
    "Samuel", "Alexander", "Gabriel", "Dylan", "Lucas", "Mason", "Logan",
    "Jennifer", "Jessica", "Sarah", "Stephanie", "Rebecca", "Rachel",
    "Samantha", "Katherine", "Emily", "Hannah", "Megan", "Lauren", "Brittany",
    "Amanda", "Melissa", "Michelle", "Kimberly", "Angela", "Heather",
    "Nicole", "Elizabeth", "Olivia", "Sophia", "Isabella", "Charlotte",
    "Amelia", "Abigail", "Madison", "Chloe", "Natalie", "Victoria", "Diana",
    "Jonathan", "Christine", "Catherine", "Patricia", "Deborah", "Cynthia",
}


def proper_nouns() -> frozenset[str]:
    """Lowercased set of bundled proper nouns to protect from flattening."""
    out: set[str] = set()
    for group in (_TECH, _PLACES_COUNTRY, _PLACES_CITY, _PLACES_REGION, _NAMES):
        out.update(w.lower() for w in group)
    return frozenset(out)


# Precomputed once at import — the lists are static.
PROPER_NOUNS: frozenset[str] = proper_nouns()
