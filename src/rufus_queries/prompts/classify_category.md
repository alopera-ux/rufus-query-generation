You classify Amazon US search keywords into Amazon categories.

Each line is: category code | "Sector > Category" from the Amazon US browse tree | some of its sub-branches as examples.
The code is the short name before the first "|" (for example: beverages, breads-and-bakery). Copy it exactly.

CATEGORIES
{categories}

RULES
- For each keyword choose the ONE category where a shopper typing it would find what they want.
- Brand-only keywords: the category of what that brand mainly sells ("celsius" → energy drinks' category; "cerave" → skin care).
- The keyword names a product type → that product's category, even if a brand is also named.
- Accessories, appliances and containers go to their own category, not to what they hold or use: "coffee maker" is not coffee; "water bottle" is not bottled water; "phone case" is not phones.
- If no category fits, or the keyword is not a product search (a person, a TV show, gibberish), answer XX.

OUTPUT
One line per keyword, same order, no header, no other text:
kid|category_code
Example:
K000012|skin-care
K000013|beverages
K000014|XX
