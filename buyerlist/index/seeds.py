"""Curated seed universe of lower-middle-market PE firms.

Why curated rather than search-discovered: web search for "lower middle market
PE firms in <sector>" returns M&A advisory and deal-marketplace content, not
sponsor websites. A trial run over one sector produced a single firm — and it
was a $150B global manager. It cost $0.80 for that one wrong answer.

The hallucination risk that pushed toward search is handled downstream instead,
by a three-stage filter that no invented firm survives:

  1. The homepage must actually resolve (verify_seeds below).
  2. The site must yield a FundProfile with real criteria when scraped (build.py).
     A name with no investment-criteria page does not become an index entry.
  3. Twenty entries get hand-checked against their source pages (index/qa.py).

So the model never asserts a firm into the index; it only reads sites that exist.
Nothing here is a claim about a firm's criteria — those come from its own pages.

`discovered_via` is a coverage hint for reporting, not a classification: these
firms are generalists as often as specialists.
"""

from __future__ import annotations

SEED_FIRMS: list[tuple[str, str, str]] = [
    # (firm name, homepage, sector hint)
    ("Alpine Investors", "https://alpineinvestors.com", "software and services"),
    ("Audax Private Equity", "https://www.audaxprivateequity.com", "diversified buy-and-build"),
    ("The Riverside Company", "https://www.riversidecompany.com", "diversified lower middle market"),
    ("Blue Point Capital Partners", "https://www.bluepointcapital.com", "industrials and services"),
    ("ShoreView Industries", "https://www.shoreview.com", "niche manufacturing"),
    ("Sverica Capital Management", "https://www.sverica.com", "healthcare and industrials"),
    ("Gen Cap America", "https://www.gencapamerica.com", "manufacturing and distribution"),
    ("Argosy Private Equity", "https://www.argosypeg.com", "manufacturing and services"),
    ("LFM Capital", "https://www.lfmcapital.com", "precision manufacturing"),
    ("Graham Partners", "https://www.grahampartners.net", "advanced manufacturing"),
    ("MiddleGround Capital", "https://www.middleground.com", "industrials and B2B"),
    ("Nautic Partners", "https://www.nautic.com", "healthcare, industrials, services"),
    ("Summit Park", "https://www.summitpark.com", "niche manufacturing and services"),
    ("Frontenac Company", "https://www.frontenac.com", "industrials and distribution"),
    ("Prospect Partners", "https://www.prospect-partners.com", "niche services"),
    ("The Watermill Group", "https://www.watermill.com", "industrial manufacturing"),
    ("Huron Capital", "https://www.huroncapital.com", "buy-and-build services"),
    ("Rotunda Capital Partners", "https://www.rotundacapital.com", "distribution and logistics"),
    ("Trivest Partners", "https://www.trivest.com", "founder-owned businesses"),
    ("Sentinel Capital Partners", "https://www.sentinelpartners.com", "diversified"),
    ("Kidd & Company", "https://www.kiddcompany.com", "industrials"),
    ("CORE Industrial Partners", "https://www.coreipfund.com", "manufacturing and industrial tech"),
    ("Shorehill Capital", "https://www.shorehillcapital.com", "industrial services"),
    ("Pfingsten Partners", "https://www.pfingsten.com", "manufacturing and distribution"),
    ("Svoboda Capital Partners", "https://www.svobodacapital.com", "distribution and services"),
    ("Wynnchurch Capital", "https://www.wynnchurch.com", "industrials"),
    ("May River Capital", "https://www.mayrivercapital.com", "precision industrial"),
    ("Align Capital Partners", "https://www.aligncp.com", "services and distribution"),
    ("Gauge Capital", "https://www.gaugecapital.com", "services and healthcare"),
    ("O2 Investment Partners", "https://www.o2investment.com", "diversified lower middle market"),
    ("Bertram Capital", "https://www.bertramcapital.com", "lower middle market growth"),
    ("Clearview Capital", "https://www.clearviewcap.com", "founder-owned services"),
    ("Incline Equity Partners", "https://www.inclineequity.com", "distribution and services"),
    ("Levine Leichtman Capital Partners", "https://www.llcp.com", "franchising and services"),
    ("Mangrove Equity Partners", "https://www.mangroveequity.com", "lower middle market"),
    ("Comvest Partners", "https://www.comvest.com", "diversified"),
    ("Periscope Equity", "https://www.periscopeequity.com", "tech-enabled services"),
    ("Osceola Capital", "https://www.osceolacapital.com", "business services"),
    ("Cyprium Partners", "https://www.cyprium.com", "non-control and minority"),
    ("NewSpring Capital", "https://www.newspringcapital.com", "growth and buyout"),
    ("Tonka Bay Equity Partners", "https://www.tonkabayequity.com", "manufacturing"),
    ("Granite Creek Capital Partners", "https://www.granitecreek.com", "lower middle market"),
    ("Hidden Harbor Capital Partners", "https://www.hiddenharborcapital.com", "industrials"),
    ("Kinderhook Industries", "https://www.kinderhook.com", "healthcare, environmental, automotive"),
    ("Tecum Capital", "https://www.tecumcapital.com", "lower middle market"),
    ("Centre Lane Partners", "https://www.centrelanepartners.com", "diversified"),
    ("Hamilton Robinson Capital Partners", "https://www.hrcap.com", "industrial technology"),
    ("Brookside Mezzanine / Equity", "https://www.brooksidemezz.com", "lower middle market"),
    ("Tide Rock", "https://www.tiderock.com", "industrial and services"),
    ("Broadview Group", "https://www.broadviewgroup.com", "niche manufacturing"),
    ("Blackford Capital", "https://www.blackfordcapital.com", "manufacturing and distribution"),
    ("Charter Oak Equity", "https://www.charteroakequity.com", "lower middle market"),
    ("Hammond Kennedy Whitney", "https://www.hkwinc.com", "manufacturing and services"),
    ("Tregaron Capital", "https://www.tregaron.com", "lower middle market"),
    ("Evolve Capital", "https://www.evolvecap.com", "services"),
    ("Sole Source Capital", "https://www.solesourcecapital.com", "industrial distribution"),
    ("Rock Gate Partners", "https://www.rockgatepartners.com", "lower middle market"),
    ("Merit Capital Partners", "https://www.meritcapital.com", "manufacturing and services"),
    ("Silver Oak Services Partners", "https://www.silveroaksp.com", "business and consumer services"),
    ("Hidden Creek Partners", "https://www.hiddencreekpartners.com", "industrials"),
    ("Lightview Capital", "https://www.lightviewcapital.com", "services and distribution"),
    ("Snow Phipps / Ancora", "https://www.snowphipps.com", "lower middle market"),
    ("Chicago Pacific Founders", "https://www.cpfounders.com", "healthcare services"),
    ("Varsity Healthcare Partners", "https://www.varsityhealthcare.com", "healthcare services"),
    ("Riata Capital Group", "https://www.riatacapital.com", "healthcare and consumer"),
    ("Pharos Capital Group", "https://www.pharosfunds.com", "healthcare services"),
    ("Cortec Group", "https://www.cortecgroup.com", "consumer and healthcare"),
    ("Brentwood Associates", "https://www.brentwood.com", "consumer"),
    ("Swander Pace Capital", "https://www.spcap.com", "consumer packaged goods"),
    ("North Castle Partners", "https://www.northcastlepartners.com", "health and wellness consumer"),
    ("Gridiron Capital", "https://www.gridironcapital.com", "founder-owned niche"),
    ("Shore Capital Partners", "https://www.shorecp.com", "healthcare and food microcap"),
    ("Thompson Street Capital Partners", "https://www.tscp.com", "software, healthcare, industrials"),
    ("Yellow Wood Partners", "https://www.yellowwoodpartners.com", "consumer brands"),
    ("Aterian Investment Partners", "https://www.aterianpartners.com", "industrials and distribution"),
    ("Monomoy Capital Partners", "https://www.mcpfunds.com", "manufacturing and distribution"),
    ("Dominus Capital", "https://www.dominuscap.com", "manufacturing and services"),
    ("Edgewater Capital Partners", "https://www.ecpartners.com", "specialty materials"),
    ("Nolan Capital", "https://www.nolancapital.com", "industrial services"),
    ("Dorilton Capital", "https://www.dorilton.com", "diversified"),
    ("Century Park Capital Partners", "https://www.centuryparkcapital.com", "lower middle market"),
    ("Transom Capital Group", "https://www.transomcap.com", "operational turnarounds"),
    ("Renovus Capital Partners", "https://www.renovuscapital.com", "knowledge and education services"),
    ("Tyree & D'Angelo Partners", "https://www.tdpfund.com", "healthcare services roll-ups"),
    ("Assembly Health / Assembly", "https://www.assemblyhealth.com", "healthcare services"),
    ("BlueHalo / Arlington", "https://www.arlingtoncap.com", "government services"),
    ("Godspeed Capital", "https://www.godspeedcapital.com", "government and infrastructure services"),
    ("Bow River Capital", "https://www.bowrivercapital.com", "lower middle market diversified"),
    ("Kian Capital Partners", "https://www.kiancapital.com", "services and industrials"),
    ("Susquehanna Private Capital", "https://www.sig.com", "diversified"),
    ("FFL Partners", "https://www.fflpartners.com", "healthcare and services"),
    ("Silver Lining / Mill Point", "https://www.millpoint.com", "industrials and services"),
    ("Soundcore Capital Partners", "https://www.soundcorecap.com", "fragmented industry roll-ups"),
    ("Trilantic North America", "https://www.trilantic.com", "diversified"),
    ("Sheridan Capital Partners", "https://www.sheridancp.com", "healthcare services"),
    ("Linden Capital Partners", "https://www.lindenllc.com", "healthcare"),
    ("Sterling Partners", "https://www.sterlingpartners.com", "education and healthcare"),
    ("WindRose Health Investors", "https://www.windrosehi.com", "healthcare services"),
    ("Lorient Capital", "https://www.lorientcapital.com", "healthcare services"),
    ("Grant Avenue Capital", "https://www.grantavenuecapital.com", "healthcare services"),
    ("Cressey & Company", "https://www.cresseyco.com", "healthcare services"),
    # Consumer and food
    ("Encore Consumer Capital", "https://www.encoreconsumercapital.com", "consumer goods"),
    ("Arbor Investments", "https://www.arborpic.com", "food and beverage"),
    ("Kainos Capital", "https://www.kainoscapital.com", "food and consumer"),
    ("Wind Point Partners", "https://www.windpointpartners.com", "consumer and industrial"),
    ("Centre Partners", "https://www.centrepartners.com", "consumer and food"),
    ("Bregal Partners", "https://www.bregalpartners.com", "food and consumer services"),
    ("Monogram Capital Partners", "https://www.monogramcapital.com", "consumer brands"),
    ("Stride Consumer Partners", "https://www.strideconsumer.com", "consumer brands"),
    ("Butterfly Equity", "https://www.butterflyequity.com", "food and agriculture"),
    ("Bansk Group", "https://www.banskgroup.com", "consumer brands"),
    ("Highlander Partners", "https://www.highlander-partners.com", "consumer and materials"),
    ("Freeman Spogli & Co.", "https://www.freemanspogli.com", "consumer and distribution"),
    ("Prospect Hill Growth Partners", "https://www.prospecthillgrowth.com", "consumer and health"),
    ("Palladium Equity Partners", "https://www.palladiumequity.com", "consumer and services"),
    # Franchising and multi-unit
    ("Roark Capital Group", "https://www.roarkcapital.com", "franchising and multi-unit"),
    ("Garnett Station Partners", "https://www.garnettstation.com", "franchising and multi-unit"),
    ("Argonne Capital Group", "https://www.argonnecapital.com", "restaurant franchising"),
    ("Atlantic Street Capital", "https://www.atlanticstreetcapital.com", "franchising"),
    # Business services
    ("The Halifax Group", "https://www.thehalifaxgroup.com", "business services"),
    ("MSouth Equity Partners", "https://www.msouth.com", "business services and industrials"),
    ("CenterOak Partners", "https://www.centeroakpartners.com", "services and industrials"),
    ("Trinity Hunt Partners", "https://www.trinityhunt.com", "business services roll-ups"),
    ("Carousel Capital", "https://www.carouselcapital.com", "business and healthcare services"),
    ("Falfurrias Capital Partners", "https://www.falfurriascapital.com", "business services"),
    ("Gemspring Capital", "https://www.gemspring.com", "services and technology"),
    ("Fort Point Capital", "https://www.fortpointcapital.com", "business services"),
    ("Tailwind Capital", "https://www.tailwind.com", "services and technology"),
    ("Lincolnshire Management", "https://www.lincolnshiremgmt.com", "diversified"),
    ("Nonantum Capital Partners", "https://www.nonantumcapital.com", "services and industrials"),
    ("Post Capital Partners", "https://www.postcp.com", "business services and manufacturing"),
    ("Seaport Capital", "https://www.seaportcapital.com", "communications and business services"),
    # IT and software services
    ("Mainsail Partners", "https://www.mainsailpartners.com", "bootstrapped software"),
    ("Serent Capital", "https://www.serentcapital.com", "vertical software and services"),
    ("Diversis Capital", "https://www.diversis.com", "software and tech services"),
    ("Banneker Partners", "https://www.bannekerpartners.com", "enterprise software"),
    ("Clearhaven Partners", "https://www.clearhavenpartners.com", "software and tech services"),
    ("ParkerGale Capital", "https://www.parkergale.com", "founder-owned technology"),
    ("Luminate Capital Partners", "https://www.luminatecapital.com", "enterprise software"),
    ("Strattam Capital", "https://www.strattam.com", "B2B software"),
    ("Sunstone Partners", "https://www.sunstonepartners.com", "tech-enabled services"),
    ("Rubicon Technology Partners", "https://www.rubicontechpartners.com", "enterprise software"),
    ("BV Investment Partners", "https://www.bvlp.com", "tech and business services"),
    ("Marlin Equity Partners", "https://www.marlinequity.com", "technology carve-outs"),
    ("Riverside Partners", "https://www.riversidepartners.com", "healthcare and technology"),
    ("Pamlico Capital", "https://www.pamlicocapital.com", "communications and tech services"),
    # Transportation and logistics
    ("Greenbriar Equity Group", "https://www.greenbriarequity.com", "transportation"),
    ("HCI Equity Partners", "https://www.hciequity.com", "distribution and logistics"),
    ("ATL Partners", "https://www.atlpartners.com", "aerospace, transportation, logistics"),
    ("Dunes Point Capital", "https://www.dunespointcapital.com", "industrials and logistics"),
    # Building products and construction services
    ("The Sterling Group", "https://www.sterling-group.com", "industrials and building products"),
    ("Angeles Equity Partners", "https://www.angelesequity.com", "industrial transformation"),
    ("Guardian Capital Partners", "https://www.guardiancp.com", "niche manufacturing"),
    ("Rock Island Capital", "https://www.rockislandcapital.com", "construction services"),
    ("Crest Rock Partners", "https://www.crestrock.com", "industrial and business services"),
    ("Sky Island Capital", "https://www.skyislandcapital.com", "niche manufacturing"),
    # Environmental and infrastructure services
    ("Bernhard Capital Partners", "https://www.bernhardcapital.com", "energy and infrastructure"),
    ("CenterGate Capital", "https://www.centergatecapital.com", "lower middle market industrials"),
    ("Turnspire Capital Partners", "https://www.turnspire.com", "industrial carve-outs"),
    ("Blue Wolf Capital Partners", "https://www.bluewolfcapital.com", "industrials"),
    ("Speyside Equity", "https://www.speysideequity.com", "specialty chemicals and industrials"),
    # Aerospace, defense, government services
    ("AE Industrial Partners", "https://www.aeroequity.com", "aerospace and defense"),
    ("Liberty Hall Capital Partners", "https://www.libertyhallcapital.com", "aerospace"),
    ("Acorn Growth Companies", "https://www.acorngrowthcompanies.com", "aerospace and defense"),
    ("Bluestone Investment Partners", "https://www.bluestoneip.com", "defense services"),
    ("Enlightenment Capital", "https://www.enlightenmentcapital.com", "defense and government"),
    ("Sagewind Capital", "https://www.sagewindcapital.com", "government services and technology"),
    ("DC Capital Partners", "https://www.dccapitalpartners.com", "government services"),
    ("Vance Street Capital", "https://www.vancestreetcapital.com", "aerospace and medical"),
    # Education and knowledge services
    ("Leeds Equity Partners", "https://www.leedsequity.com", "education and training"),
    ("Quad Partners", "https://www.quadpartners.com", "education services"),
    ("The Wicks Group", "https://www.wicksgroup.com", "media, education, information"),
    ("Achieve Partners", "https://www.achievepartners.com", "education and workforce services"),
    # Specialty distribution and automotive aftermarket
    ("Bunker Hill Capital", "https://www.bunkerhillcapital.com", "distribution and manufacturing"),
    ("New Heritage Capital", "https://www.newheritagecapital.com", "founder-owned businesses"),
    ("Wingate Partners", "https://www.wingatepartners.com", "distribution and services"),
    ("Mountaingate Capital", "https://www.mountaingatecapital.com", "marketing and distribution"),
    ("Stone Arch Capital", "https://www.stonearchcapital.com", "manufacturing and services"),
    ("Highview Capital", "https://www.highviewcap.com", "special situations"),
    # Packaging and specialty materials
    ("Mason Wells", "https://www.masonwells.com", "packaging and engineered products"),
    ("SK Capital Partners", "https://www.skcapitalpartners.com", "specialty materials and pharma"),
    ("Arsenal Capital Partners", "https://www.arsenalcapital.com", "specialty industrials"),
    ("Altus Capital Partners", "https://www.altuscapitalpartners.com", "precision manufacturing"),
    ("Wellspring Capital Management", "https://www.wellspringcapital.com", "manufacturing"),
    ("Insight Equity", "https://www.insightequity.com", "industrials and materials"),
    # Energy services
    ("SCF Partners", "https://www.scfpartners.com", "oilfield services"),
    ("Turnbridge Capital Partners", "https://www.turnbridgecapital.com", "energy services"),
    ("Pelican Energy Partners", "https://www.pelicanenergypartners.com", "nuclear services"),
    ("Hastings Equity Partners", "https://www.hastingsequity.com", "energy services"),
    # Insurance and financial services
    ("Aquiline Capital Partners", "https://www.aquiline.com", "insurance and financial services"),
    ("Flexpoint Ford", "https://www.flexpointford.com", "financial services and healthcare"),
    ("Lightyear Capital", "https://www.lightyearcapital.com", "financial services"),
    ("Stone Point Capital", "https://www.stonepoint.com", "insurance and financial services"),
    # Facilities and consumer services
    ("Boyne Capital", "https://www.boynecapital.com", "founder-owned lower middle market"),
    ("Pine Tree Equity Partners", "https://www.pinetreeequity.com", "services and healthcare"),
    ("Palm Beach Capital", "https://www.palmbeachcapital.com", "lower middle market services"),
    ("Blue Sage Capital", "https://www.bluesage.com", "niche manufacturing and services"),
    # Diversified industrials and special situations
    ("Atlas Holdings", "https://www.atlasholdingsllc.com", "industrial turnarounds"),
    ("Stellex Capital Management", "https://www.stellexcapital.com", "industrials and aerospace"),
    ("One Rock Capital Partners", "https://www.onerockcapital.com", "industrials and chemicals"),
    ("Littlejohn & Co.", "https://www.littlejohnllc.com", "industrials and special situations"),
    ("Peak Rock Capital", "https://www.peakrockcapital.com", "diversified middle market"),
    ("Trive Capital", "https://www.trivecapital.com", "complex industrials"),
    ("Kingswood Capital Management", "https://www.kingswoodcapital.com", "special situations"),
    ("Pritzker Private Capital", "https://www.ppcpartners.com", "family-owned manufacturing"),
    ("Norwest Equity Partners", "https://www.nep.com", "middle market diversified"),
    ("Goldner Hawn", "https://www.goldnerhawn.com", "founder-owned middle market"),
    ("Spell Capital Partners", "https://www.spellcapital.com", "niche manufacturing"),
    ("Waud Capital Partners", "https://www.waudcapital.com", "healthcare and software services"),
    ("Tenex Capital Management", "https://www.tenexcm.com", "industrials and healthcare"),
    ("Ridgemont Equity Partners", "https://www.ridgemontep.com", "industrials and services"),
    # Healthcare services
    ("Revelstoke Capital Partners", "https://www.revelstokecapital.com", "healthcare services"),
    ("InTandem Capital Partners", "https://www.intandemcapital.com", "healthcare services"),
    ("Havencrest Capital Management", "https://www.havencrest.com", "healthcare services"),
    ("Beecken Petty O'Keefe & Company", "https://www.bpoc.com", "healthcare"),
    ("Vesey Street Capital Partners", "https://www.vscp.com", "healthcare services"),
    ("BelHealth Investment Partners", "https://www.belhealth.com", "healthcare services"),
    # Canadian lower middle market
    ("TorQuest Partners", "https://www.torquest.com", "Canadian middle market"),
    ("Ironbridge Equity Partners", "https://www.ironbridgeequity.com", "Canadian buyouts"),
    ("Novacap", "https://www.novacap.ca", "industries and tech services"),
]


def as_seed_rows() -> list[dict]:
    """Shape the curated list like discovery output so build.py is unchanged."""
    from .discover import _domain

    rows = []
    for name, url, sector in SEED_FIRMS:
        rows.append(
            {
                "firm_name": name,
                "website": url,
                "domain": _domain(url),
                "hq_hint": None,
                "why_lmm": "curated seed; criteria verified by scraping the firm's own site",
                "discovered_via": sector,
                "source": "curated",
            }
        )
    return rows
