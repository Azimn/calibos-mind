"""Wardrobe: four text-described outfits, no images, no catalogue.

Adapted from the Cozy Corner v3 garment data (same names and tactile
descriptions for the four looks Calibos's house keeps), cut down from eight
looks to four on the simple-first rule: the wardrobe is an option, not a
foundation. Garments are physical objects with a slot, a description, and a
touch. A look is a suggested combination, never a mandatory costume.
"""
from __future__ import annotations

SLOTS = ("outerwear", "top", "waist", "trousers", "neckwear", "accessory",
         "footwear")

# label, slot, material description, tactile observation
GARMENTS = {
    "tweed_waistcoat": (
        "Brown herringbone waistcoat", "waist",
        "Brown herringbone vest with dark buttons, small watch chain and "
        "breast pocket.",
        "The tweed has a fine nubbly weave."),
    "rolled_white_shirt": (
        "Rolled-sleeve white shirt", "top",
        "Cream white collared shirt with sleeves rolled neatly above the "
        "elbows.",
        "Breathable cotton feels light at the wrists."),
    "wine_bowtie": (
        "Wine-red bow tie", "neckwear",
        "Compact burgundy bow tie with a soft satin finish.",
        "The knot stays comfortably in place."),
    "satchel": (
        "Brown crossbody leather satchel", "accessory",
        "A small brown leather satchel with brass fittings and long "
        "crossbody strap.",
        "Its strap has a worn, polished edge and the clasp gives a small "
        "click."),
    "herringbone_pants": (
        "Brown herringbone trousers", "trousers",
        "Warm-brown patterned tailored trousers with rolled hems.",
        "The cloth has a small raised herringbone texture."),
    "denim_jacket": (
        "Faded blue denim jacket", "outerwear",
        "Worn blue denim jacket with large seams and rolled sleeves.",
        "The denim is stiff at the seams and soft from years of wear "
        "elsewhere."),
    "plaid_overshirt": (
        "Red and black flannel shirt", "waist",
        "Open red-and-black checked flannel worn as a middle layer.",
        "Brushed flannel feels warm and lightly fuzzy."),
    "graphic_tee": (
        "Cream starship graphic T-shirt", "top",
        "Cream graphic T-shirt carrying a small colorful science-fiction "
        "insignia.",
        "The jersey cotton is soft and familiar."),
    "tan_casual_pants": (
        "Loose tan rolled trousers", "trousers",
        "Relaxed tan pants with rolled hems and a worn leather belt.",
        "The cotton has a lived-in softness and the belt is smooth."),
    "black_tweed_jacket": (
        "Dark textured blazer", "outerwear",
        "Near-black textured tailored jacket, subtle red pocket square, "
        "notched lapels.",
        "Dense cloth with a lightly rough grain."),
    "black_collar_shirt": (
        "Cream stand-collar shirt", "top",
        "Cream dress shirt with upright collar beneath layers.",
        "Thin cotton feels smooth beneath heavier fabric."),
    "dark_waistcoat": (
        "Dark waistcoat with watch chain", "waist",
        "Deep plum patterned waistcoat with brass watch chain.",
        "Firm woven cloth and a smooth metal chain."),
    "tartan_scarf": (
        "Burgundy tartan scarf", "neckwear",
        "Red, black and charcoal plaid scarf wrapped loosely at the neck.",
        "The wool is soft and warm, with fringed ends."),
    "dark_tweed_pants": (
        "Dark herringbone trousers", "trousers",
        "Deep charcoal trousers, close fitted through the calf.",
        "The lightly patterned cloth drapes without stiffness."),
    "ivory_lab_coat": (
        "Ivory laboratory coat", "outerwear",
        "Knee-length off-white work coat, broad pockets, and pens in the "
        "breast pocket.",
        "The washable cotton twill is thick, soft from use and lightly "
        "creased."),
    "open_blue_shirt": (
        "Open-collar slate-blue shirt", "top",
        "Pale slate-blue open-collar button shirt, relaxed at the neck.",
        "The cotton is smooth and cool, with a little wear at the cuff."),
    "charcoal_trousers": (
        "Charcoal rolled-cuff trousers", "trousers",
        "Dark charcoal tailored trousers with relaxed, turned-up cuffs.",
        "The twill holds a fold but moves readily."),
}

LOOKS = {
    "waistcoat": {
        "name": "Herringbone waistcoat and satchel",
        "pieces": ["tweed_waistcoat", "rolled_white_shirt", "wine_bowtie",
                   "satchel", "herringbone_pants"],
    },
    "denim": {
        "name": "Denim and checked flannel",
        "pieces": ["denim_jacket", "plaid_overshirt", "graphic_tee",
                   "tan_casual_pants"],
    },
    "plaid": {
        "name": "Dark jacket and tartan scarf",
        "pieces": ["black_tweed_jacket", "black_collar_shirt",
                   "dark_waistcoat", "tartan_scarf", "dark_tweed_pants"],
    },
    "lab": {
        "name": "Ivory laboratory coat",
        "pieces": ["ivory_lab_coat", "open_blue_shirt", "charcoal_trousers"],
    },
}


def initial_wardrobe_state() -> dict:
    """Fresh wardrobe state: nothing worn, no look chosen."""
    return {"worn": {}, "current_look": None}


def describe_worn(worn: dict, current_look: str | None = None) -> str:
    """First-person sentence naming what is currently worn.

    When a whole look is worn, name the look only ("wearing the
    herringbone waistcoat and satchel outfit") — the scene repeats every
    visit, and a five-item inventory in every scene is exhausting. The
    full item list is for the moment of change (the wear texts already
    name what changed) or an explicit look at the wardrobe, so pass no
    look for the detailed form. A mix-and-match outfit has no look name,
    so it always lists its pieces.
    """
    if not worn:
        return ""
    if current_look is not None and current_look in LOOKS:
        return ("I am wearing the "
                + LOOKS[current_look]["name"].lower() + " outfit.")
    names = [GARMENTS[gid][0].lower() for _, gid in sorted(worn.items())
             if gid in GARMENTS]
    if not names:
        return ""
    return "I am wearing " + ", ".join(names) + "."
