"""
accounts.py — Thomas Scientific 80s Accounts
All accounts organised by the 7 industry categories.
Import ACCOUNTS to get the full dict, or use helpers below.
"""

ACCOUNTS = {
    "Education & Research": [
        "ARIZONA STATE UNIVERSITY", "BAYLOR COLLEGE OF MEDICINE", "BROAD INSTITUTE",
        "COLORADO MESA UNIVERSITY", "DREXEL UNIVERSITY", "DUKE UNIVERSITY",
        "EMORY UNIVERSITY", "EPFL", "HARVARD UNIVERSITY", "HHMI",
        "INDIANA UNIVERSITY", "JACKSON LABS", "JOHNS HOPKINS UNIVERSITY",
        "LABCENTRAL", "LOUISIANA STATE UNIVERSITY", "MASSACHUSETTS INSTITUTE OF TEC",
        "MICHIGAN STATE UNIVERSITY", "NEW YORK UNIVERSITY", "OHIO STATE UNIVERSITY",
        "PENN STATE UNIVERSITY", "ROCKEFELLER UNIVERSITY", "STANFORD UNIVERSITY",
        "TEMPLE UNIVERSITY", "UCOP", "UNIVERSITY OF ARIZONA", "UNIVERSITY OF CINCINNATI",
        "UNIVERSITY OF CONNECTICUT", "UNIVERSITY OF ILLINOIS", "UNIVERSITY OF MARYLAND",
        "UNIVERSITY OF MIAMI", "UNIVERSITY OF MICHIGAN", "UNIVERSITY OF OREGON",
        "UNIVERSITY OF PENNSYLVANIA", "UNIVERSITY OF TORONTO", "UNIVERSITY OF UTAH",
        "UNIVERSITY OF WASHINGTON", "VANDERBILT UNIVERSITY", "WEILL CORNELL MEDICAL COLLEGE",
        "YALE UNIVERSITY",
        "ALLEGHENY SINGER RESEARCH INSTITUTE", "CORIELL INSTITUTE", "ATCC",
        "AMERICAN SOCIETY FOR MICROBIOL", "BHN RESEARCH", "STANFORD LINEAR",
        "STANFORD EQUIPMENT",
    ],
    "BioPharma": [
        "ABBOTT", "ABBVIE", "ADAPTIMMUNE", "ALK-ABELLO", "ALVOTECH",
        "AMNEAL PHARMACEUTICALS", "AMPHASTAR", "ARVINAS", "ASTRAZENECA",
        "CIVITAS THERAPEUTICS", "ELANCO", "ELI LILLY", "EYEPOINT",
        "FREENOME HOLDINGS INC", "GENEZEN", "GRANULES PHARMACEUTICALS INC",
        "HIKMA PHARMACEUTICALS", "LOXO ONCOLOGY", "MANNKIND CORPORATION", "MERCK",
        "MODERNA THERAPEUTICS", "MYRIAD", "NOVONESIS", "PFIZER", "PIRAMAL",
        "POSEIDA THERAPEUTICS", "REGENERON", "ROCHE GROUP", "SANOFI",
        "SEPIA THERAPEUTICS", "STALLERGENE GREER", "SYNTHEGO", "TAKEDA",
        "TCR2 THERAPEUTICS", "TERGUS PHARMA", "TILRAY", "TRIANA BIOMEDICINES",
        "TWIST BIOSCIENCES", "VALENT BIOSCIENCES", "VANDA PHARMACEUTICALS",
        "VERICEL CORPORATION", "VERTEX PHARMACEUTICAL", "VIOME",
        "CYTOIMMUNE THERAPEUTICS OF PR", "OSIRIS THERAPEUTICS", "CRYOLIFE",
        "LOTTE BIOLOGICS", "MATICA BIO", "LANDMARK BIO", "SYCAMORE LIFE SCIENCES",
        "GENISTA BIOSCIENCES", "ATUM",
    ],
    "CDMO / CRO": [
        "AJINOMOTO ALTHEA", "CHARLES RIVER LABS", "CONTRACT PHARMACAL", "CURIA",
        "HOVIONE FARMACIENCIA SA", "INOTIV", "KBI BIOPHARMA", "MEDPACE", "NAMSA",
        "PPD", "IQVIA", "ABSORPTION SYSTEMS INC", "CELLARES", "BIOAGILYTIX",
        "ADIMAB", "AMERICAN CELL TECHNOLOGY", "AVEVA DRUG DELIVERY SYSTEMS",
    ],
    "Clinical / Mol Dx": [
        "AMBRY", "BAYLOR GENETICS", "BIOMERIEUX", "BODE TECHNOLOGY GROUP",
        "CLINICAL REFERENCE LABORATORY", "COOPER GENOMICS", "EUROFINS",
        "EXACT SCIENCES", "GENE BY GENE", "GENOVA DIAGNOSTICS", "GRAIL",
        "GUARDANT HEALTH", "HISTOGENETICS", "LABCORP", "LGC", "NATERA", "NEOGEN",
        "NEW ENGLAND BIO LABS", "PACE ANALYTICAL", "PHENOMENEX", "PROMEGA",
        "SCIENTIA DX", "SELUX DIAGNOSTICS", "SIEMENS HEALTHCARE DIAGNOSTICS",
        "SPECTRUM LABORATORIES", "TEMPUS", "TRANSNETYX", "DANAHER BECKMAN",
        "DANAHER CYTIVA", "DANAHER IDT", "ROMER LABS", "SUBURBAN TESTING LABS",
        "COLORADO ANALYTICAL LABS", "CUMBERLAND VALLEY ANALYTICAL S",
        "EAGLE ANAYTICAL SERVICES", "GENEXPRESS", "ARUP", "KAYCHA LABS", "SC LABS",
        "SALIMETRICS", "CORGENIX MEDICAL CORPORATION", "IVF STORE",
    ],
    "Hospital & Health Systems": [
        "BETH ISRAEL", "CEDAR SINAI MEDICAL CENTER", "CHILDRENS HOSP OF CINCINNATI",
        "CHOP", "DANA FARBER CANCER INSTITUTE", "H LEE MOFFITT CANCER CENTER",
        "HACKENSACK UNIVERSITY MEDICAL", "HOSPITAL FOR SPECIAL SURGERY",
        "KAISER PERMANENTE", "MAYO", "MD ANDERSON", "PARTNERS HEALTHCARE SYSTEM",
        "VANDERBILT MEDICAL CENTER", "CCF", "LA JOLLA INFECT DISEASE INST",
    ],
    "Industrial": [
        "APTAR", "BRASKEM", "CORNING", "EVONIK", "FERGUSON ENTERPRISES",
        "FERGUSON INDUSTRIAL", "FIRST SOLAR", "GKN", "GRAINGER", "INNOPHOS",
        "JOHNSON MATTHEY", "KLA", "MINNESOTA MINING & MFG", "MONROE ENERGY",
        "MONTROSE", "MONUMENT CHEMICAL KENTUCKY", "QORVO", "SAINT-GOBAIN",
        "SIBELCO NORTH AMERICA", "SKYWORKS", "VALSPAR CORPORATION",
        "W L GORE & ASSOCIATES", "W L GORE & ASSOC INC", "WEST PHARMACEUTICAL SERVICE",
        "COOPERVISION", "GLAUKOS CORPORATION", "STAAR SURGICAL",
        "NITINOL DEVICES & COMPONENTS", "TERUMO", "CONFLUENT MEDICAL",
        "CIRTEC MEDICAL", "ORGAN RECOVERY SYSTEMS", "DANAHER CHEMTREAT",
        "DANAHER XRITE", "AMERICAN PACIFIC CORPORATION", "INTERPLASTIC CORPORATION",
        "AMERICHEM", "PARKER PROCESS", "SANI-TECH WEST", "GFS CHEMICAL",
        "PHOTRONICS", "INFINERA", "VEECO", "FLIR SYSTEMS", "FUJI",
        "HRL LABORATORIES", "TELEDYNE SCIENTIFIC", "SOLAERO TECHNOLOGIES CORP",
        "AXONICS MODULATION",
    ],
    "Government": [
        "DEFENSE LOGISTICS (DLA)", "DEFENSE SUPPLY CENTER", "LEIDOS", "NIH",
        "PERATON", "US ARMY", "USDA", "VETERANS ADMINISTRATION",
        "CA DEPT OF JUSTICE", "WV DEPT OF AGRICULTURE", "CITY OF CHICAGO",
        "CITY OF COLUMBUS", "CITY OF COLUMBUS OHIO", "AMAZON MARKET PLACE",
    ],
}

# ── Account aliases ───────────────────────────────────────────────
# Maps internal account names to known alternate names, abbreviations,
# and rebrands so Gemini prompts and NIH/NSF merge matching find the right company.
ACCOUNT_ALIASES = {
    # Education & Research
    "MASSACHUSETTS INSTITUTE OF TEC": ["MIT", "Massachusetts Institute of Technology"],
    "HHMI":                           ["Howard Hughes Medical Institute"],
    "UCOP":                           ["University of California", "UC System", "UC Office of the President"],
    "STANFORD LINEAR":                ["SLAC", "SLAC National Accelerator Laboratory"],
    "BROAD INSTITUTE":                ["Broad Institute of MIT and Harvard"],
    "JACKSON LABS":                   ["JAX", "The Jackson Laboratory"],
    "AMERICAN SOCIETY FOR MICROBIOL": ["ASM", "American Society for Microbiology"],
    "WEILL CORNELL MEDICAL COLLEGE":  ["Weill Cornell Medicine"],

    # BioPharma
    "MODERNA THERAPEUTICS":           ["Moderna", "Moderna Inc"],
    "ROCHE GROUP":                    ["Roche", "Genentech", "F. Hoffmann-La Roche"],
    "ELI LILLY":                      ["Lilly", "Eli Lilly and Company"],
    "NOVONESIS":                      ["Novozymes", "Chr. Hansen"],
    "LOXO ONCOLOGY":                  ["Loxo@Lilly", "Loxo Oncology at Lilly"],
    "VERTEX PHARMACEUTICAL":          ["Vertex Pharmaceuticals"],
    "TWIST BIOSCIENCES":              ["Twist Bioscience"],
    "MANNKIND CORPORATION":           ["MannKind"],
    "ALK-ABELLO":                     ["ALK", "ALK Abello"],
    "FREENOME HOLDINGS INC":          ["Freenome"],
    "GRANULES PHARMACEUTICALS INC":   ["Granules India", "Granules Pharmaceuticals"],

    # CDMO / CRO
    "CHARLES RIVER LABS":             ["Charles River Laboratories", "CRL"],
    "HOVIONE FARMACIENCIA SA":        ["Hovione"],
    "PPD":                            ["PPD Inc", "Thermo Fisher PPD"],
    "IQVIA":                          ["IQVIA Holdings", "IMS Health"],
    "ABSORPTION SYSTEMS INC":         ["Absorption Systems"],
    "AJINOMOTO ALTHEA":               ["Althea Technologies", "Ajinomoto Bio-Pharma Services"],

    # Clinical / Mol Dx
    "DANAHER BECKMAN":                ["Beckman Coulter"],
    "DANAHER CYTIVA":                 ["Cytiva", "GE Healthcare Life Sciences"],
    "DANAHER IDT":                    ["IDT", "Integrated DNA Technologies"],
    "NEW ENGLAND BIO LABS":           ["NEB", "New England Biolabs"],
    "SIEMENS HEALTHCARE DIAGNOSTICS": ["Siemens Healthineers"],
    "CUMBERLAND VALLEY ANALYTICAL S": ["Cumberland Valley Analytical Services", "CVAS"],
    "EAGLE ANAYTICAL SERVICES":       ["Eagle Analytical Services"],

    # Hospital & Health Systems
    "BETH ISRAEL":                    ["Beth Israel Deaconess", "BIDMC", "Beth Israel Deaconess Medical Center"],
    "CEDAR SINAI MEDICAL CENTER":     ["Cedars-Sinai", "Cedars Sinai"],
    "CHILDRENS HOSP OF CINCINNATI":   ["Cincinnati Children's", "CCHMC", "Cincinnati Children's Hospital Medical Center"],
    "CHOP":                           ["Children's Hospital of Philadelphia"],
    "PARTNERS HEALTHCARE SYSTEM":     ["Mass General Brigham", "MGB", "Partners HealthCare"],
    "CCF":                            ["Cleveland Clinic", "Cleveland Clinic Foundation"],
    "H LEE MOFFITT CANCER CENTER":    ["Moffitt Cancer Center", "Moffitt"],
    "HACKENSACK UNIVERSITY MEDICAL":  ["Hackensack Meridian Health"],
    "MD ANDERSON":                    ["MD Anderson Cancer Center", "University of Texas MD Anderson"],
    "DANA FARBER CANCER INSTITUTE":   ["Dana-Farber", "Dana Farber"],
    "LA JOLLA INFECT DISEASE INST":   ["La Jolla Institute for Immunology", "LJI"],

    # Industrial
    "MINNESOTA MINING & MFG":         ["3M", "3M Company"],
    "W L GORE & ASSOCIATES":          ["Gore", "W.L. Gore", "Gore-Tex"],
    "W L GORE & ASSOC INC":           ["Gore", "W.L. Gore"],
    "DANAHER CHEMTREAT":              ["ChemTreat"],
    "DANAHER XRITE":                  ["X-Rite", "X-Rite Pantone"],
    "FLIR SYSTEMS":                   ["Teledyne FLIR", "FLIR"],
    "NITINOL DEVICES & COMPONENTS":   ["NDC", "Nitinol Devices"],
    "SOLAERO TECHNOLOGIES CORP":      ["SolAero Technologies"],

    # Government
    "DEFENSE LOGISTICS (DLA)":        ["DLA", "Defense Logistics Agency"],
    "VETERANS ADMINISTRATION":        ["VA", "Department of Veterans Affairs", "Veterans Affairs"],
    "AMAZON MARKET PLACE":            ["Amazon Business", "Amazon"],
    "CA DEPT OF JUSTICE":             ["California DOJ", "California Department of Justice"],
    "WV DEPT OF AGRICULTURE":         ["West Virginia Department of Agriculture"],
}


# Super80 — highest priority accounts (span multiple categories)
SUPER80 = [
    "AMAZON MARKET PLACE",
    "DEFENSE LOGISTICS (DLA)",
    "IQVIA",
    "IVF STORE",
    "LABCORP",
    "TAKEDA",
    "TEMPUS",
]


def all_accounts_flat() -> list:
    """Return all accounts as a flat list of (account, category) tuples."""
    result = []
    for cat, accts in ACCOUNTS.items():
        for acct in accts:
            result.append((acct, cat))
    return result
