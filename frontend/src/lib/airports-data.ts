// Airport dataset for the From/To autocomplete. Loaded lazily by airports.ts
// (dynamic import) so it stays out of the main bundle.
//
// AIRPORTS rows: CODE|City|Airport name|Country code|aliases (comma list, may include Hebrew)
// A leading "*" on a row marks a top destination from Israel (ranking boost).
// METROS rows:   CODE|City|Country code|member airport codes|aliases
//   CODE is the IATA metro code where one exists; otherwise the members joined by "/".
// REGIONS rows:  Name|subtitle|aliases

export const COUNTRIES: Record<string, string> = {
  IL: "Israel|ישראל", CY: "Cyprus|קפריסין", GR: "Greece|יוון", TR: "Turkey|טורקיה,Türkiye",
  GB: "United Kingdom|אנגליה,בריטניה,UK,England,Britain", IE: "Ireland|אירלנד", FR: "France|צרפת",
  BE: "Belgium|בלגיה", NL: "Netherlands|הולנד,Holland", LU: "Luxembourg|לוקסמבורג", DE: "Germany|גרמניה",
  CH: "Switzerland|שוויץ", AT: "Austria|אוסטריה", IT: "Italy|איטליה", ES: "Spain|ספרד",
  PT: "Portugal|פורטוגל", MT: "Malta|מלטה", GI: "Gibraltar|", DK: "Denmark|דנמרק", SE: "Sweden|שוודיה",
  NO: "Norway|נורבגיה", FI: "Finland|פינלנד", IS: "Iceland|איסלנד", LT: "Lithuania|ליטא", LV: "Latvia|לטביה",
  EE: "Estonia|אסטוניה", PL: "Poland|פולין", CZ: "Czechia|צכיה,Czech Republic", SK: "Slovakia|סלובקיה",
  HU: "Hungary|הונגריה", RO: "Romania|רומניה", MD: "Moldova|מולדובה", BG: "Bulgaria|בולגריה",
  RS: "Serbia|סרביה", ME: "Montenegro|מונטנגרו", HR: "Croatia|קרואטיה", SI: "Slovenia|סלובניה",
  BA: "Bosnia and Herzegovina|בוסניה,Bosnia", MK: "North Macedonia|מקדוניה,Macedonia", AL: "Albania|אלבניה",
  XK: "Kosovo|קוסובו", GE: "Georgia|גאורגיה,גיאורגיה", AM: "Armenia|ארמניה", AZ: "Azerbaijan|אזרבייג'ן",
  RU: "Russia|רוסיה", BY: "Belarus|בלארוס", UZ: "Uzbekistan|אוזבקיסטן", KZ: "Kazakhstan|קזחסטן",
  KG: "Kyrgyzstan|קירגיזסטן", AE: "United Arab Emirates|איחוד האמירויות,UAE,Emirates", BH: "Bahrain|בחריין",
  QA: "Qatar|קטאר", OM: "Oman|עומאן", SA: "Saudi Arabia|ערב הסעודית", KW: "Kuwait|כווית", JO: "Jordan|ירדן",
  EG: "Egypt|מצרים", MA: "Morocco|מרוקו", TN: "Tunisia|תוניסיה", ZA: "South Africa|דרום אפריקה",
  KE: "Kenya|קניה", ET: "Ethiopia|אתיופיה", TZ: "Tanzania|טנזניה", MU: "Mauritius|מאוריציוס",
  SC: "Seychelles|סיישל", NG: "Nigeria|ניגריה", GH: "Ghana|גאנה", RW: "Rwanda|רואנדה", UG: "Uganda|אוגנדה",
  NA: "Namibia|נמיביה", ZW: "Zimbabwe|זימבבואה", MG: "Madagascar|מדגסקר", SN: "Senegal|סנגל",
  RE: "Réunion|ראוניון", IN: "India|הודו", LK: "Sri Lanka|סרי לנקה", MV: "Maldives|המלדיביים",
  NP: "Nepal|נפאל", TH: "Thailand|תאילנד", VN: "Vietnam|וייטנאם", KH: "Cambodia|קמבודיה", LA: "Laos|לאוס",
  MM: "Myanmar|מיאנמר,Burma", MY: "Malaysia|מלזיה", SG: "Singapore|סינגפור", ID: "Indonesia|אינדונזיה",
  PH: "Philippines|הפיליפינים", HK: "Hong Kong|הונג קונג", MO: "Macau|מקאו", TW: "Taiwan|טייוואן",
  CN: "China|סין", JP: "Japan|יפן", KR: "South Korea|קוריאה,Korea", MN: "Mongolia|מונגוליה",
  AU: "Australia|אוסטרליה", NZ: "New Zealand|ניו זילנד", FJ: "Fiji|פיג'י", PF: "French Polynesia|טהיטי,Tahiti",
  US: "United States|ארצות הברית,ארהב,USA,America", CA: "Canada|קנדה", MX: "Mexico|מקסיקו",
  DO: "Dominican Republic|הרפובליקה הדומיניקנית", CU: "Cuba|קובה", JM: "Jamaica|ג'מייקה",
  BS: "Bahamas|איי בהאמה", AW: "Aruba|ארובה", CW: "Curaçao|קוראסאו", BB: "Barbados|ברבדוס",
  SX: "Sint Maarten|סנט מרטן,St Maarten", PR: "Puerto Rico|פוארטו ריקו", PA: "Panama|פנמה",
  CR: "Costa Rica|קוסטה ריקה", GT: "Guatemala|גואטמלה", SV: "El Salvador|אל סלבדור",
  CO: "Colombia|קולומביה", PE: "Peru|פרו", CL: "Chile|צ'ילה", AR: "Argentina|ארגנטינה", BR: "Brazil|ברזיל",
  EC: "Ecuador|אקוודור", UY: "Uruguay|אורוגוואי", PY: "Paraguay|פרגוואי", BO: "Bolivia|בוליביה",
  VE: "Venezuela|ונצואלה",
};

export const AIRPORTS = `
*TLV|Tel Aviv|Ben Gurion|IL|תל אביב,Tel Aviv-Yafo,נתבג
*ETM|Eilat|Ramon|IL|אילת
HFA|Haifa|Haifa|IL|חיפה
*LCA|Larnaca|Larnaca International|CY|לרנקה
*PFO|Paphos|Paphos International|CY|פאפוס
ECN|Nicosia|Ercan|CY|ניקוסיה,North Cyprus
*ATH|Athens|Eleftherios Venizelos|GR|אתונה
*SKG|Thessaloniki|Makedonia|GR|סלוניקי,Salonica
*HER|Heraklion|Nikos Kazantzakis|GR|הרקליון,Crete,כרתים,Iraklio
*CHQ|Chania|Ioannis Daskalogiannis|GR|חאניה,Crete,כרתים
JSH|Sitia|Sitia|GR|Crete
*RHO|Rhodes|Diagoras|GR|רודוס,Rodos
*CFU|Corfu|Ioannis Kapodistrias|GR|קורפו,Kerkyra
*JTR|Santorini|Santorini (Thira)|GR|סנטוריני,Thira,Fira
*JMK|Mykonos|Mykonos|GR|מיקונוס
*KGS|Kos|Hippocrates|GR|קוס
*ZTH|Zakynthos|Dionysios Solomos|GR|זקינתוס,Zante
EFL|Kefalonia|Anna Pollatou|GR|קפלוניה,Cephalonia
PVK|Preveza|Aktion (Preveza/Lefkada)|GR|Lefkada,לפקדה
KLX|Kalamata|Kalamata|GR|קלמטה
JSI|Skiathos|Alexandros Papadiamantis|GR|סקיאתוס
JNX|Naxos|Naxos|GR|נקסוס
PAS|Paros|Paros|GR|פארוס
MLO|Milos|Milos|GR|מילוס
JSY|Syros|Syros|GR|
MJT|Mytilene|Odysseas Elytis|GR|Lesbos,Lesvos,לסבוס
SMI|Samos|Aristarchos of Samos|GR|סאמוס
JKH|Chios|Chios|GR|
LXS|Lemnos|Lemnos|GR|Limnos
AOK|Karpathos|Karpathos|GR|
KIT|Kythira|Kythira|GR|
KVA|Kavala|Alexander the Great|GR|קוואלה
VOL|Volos|Nea Anchialos|GR|
IOA|Ioannina|Ioannina|GR|
*IST|Istanbul|Istanbul Airport|TR|איסטנבול
*SAW|Istanbul|Sabiha Gökçen|TR|איסטנבול
*AYT|Antalya|Antalya|TR|אנטליה
ADB|Izmir|Adnan Menderes|TR|איזמיר
ESB|Ankara|Esenboğa|TR|אנקרה
DLM|Dalaman|Dalaman|TR|Fethiye,Marmaris
BJV|Bodrum|Milas-Bodrum|TR|בודרום
GZP|Alanya|Gazipaşa-Alanya|TR|
TZX|Trabzon|Trabzon|TR|
NAV|Nevşehir|Kapadokya|TR|Cappadocia,קפדוקיה
ASR|Kayseri|Erkilet|TR|Cappadocia
*LHR|London|Heathrow|GB|לונדון
*LGW|London|Gatwick|GB|לונדון
*STN|London|Stansted|GB|לונדון
*LTN|London|Luton|GB|לונדון
LCY|London|London City|GB|לונדון
SEN|London|Southend|GB|לונדון
*MAN|Manchester|Manchester|GB|מנצ'סטר
EDI|Edinburgh|Edinburgh|GB|אדינבורו
GLA|Glasgow|Glasgow|GB|גלזגו
PIK|Prestwick|Glasgow Prestwick|GB|Glasgow
BHX|Birmingham|Birmingham|GB|ברמינגהאם
BRS|Bristol|Bristol|GB|
LPL|Liverpool|John Lennon|GB|ליברפול
NCL|Newcastle|Newcastle|GB|
LBA|Leeds|Leeds Bradford|GB|Bradford
EMA|Nottingham|East Midlands|GB|
BFS|Belfast|Belfast International|GB|בלפסט,Northern Ireland
BHD|Belfast|George Best Belfast City|GB|בלפסט,Northern Ireland
ABZ|Aberdeen|Aberdeen|GB|
INV|Inverness|Inverness|GB|
CWL|Cardiff|Cardiff|GB|Wales
SOU|Southampton|Southampton|GB|
BOH|Bournemouth|Bournemouth|GB|
EXT|Exeter|Exeter|GB|
NQY|Newquay|Cornwall Airport Newquay|GB|Cornwall
*DUB|Dublin|Dublin|IE|דבלין
ORK|Cork|Cork|IE|
SNN|Shannon|Shannon|IE|
NOC|Knock|Ireland West|IE|
*CDG|Paris|Charles de Gaulle|FR|פריז,Roissy
*ORY|Paris|Orly|FR|פריז
*BVA|Paris|Beauvais-Tillé|FR|Beauvais,פריז
*NCE|Nice|Côte d'Azur|FR|ניס,Cannes,Monaco
MRS|Marseille|Provence|FR|מרסיי
LYS|Lyon|Saint-Exupéry|FR|ליון
TLS|Toulouse|Blagnac|FR|טולוז
BOD|Bordeaux|Mérignac|FR|בורדו
NTE|Nantes|Atlantique|FR|
MPL|Montpellier|Méditerranée|FR|
BIQ|Biarritz|Pays Basque|FR|
AJA|Ajaccio|Napoléon Bonaparte|FR|Corsica,קורסיקה
BIA|Bastia|Poretta|FR|Corsica,קורסיקה
FSC|Figari|Sud Corse|FR|Corsica
CLY|Calvi|Sainte-Catherine|FR|Corsica
SXB|Strasbourg|Strasbourg|FR|
LIL|Lille|Lesquin|FR|
GNB|Grenoble|Alpes-Isère|FR|
CMF|Chambéry|Savoie Mont Blanc|FR|
TLN|Toulon|Hyères|FR|
FNI|Nîmes|Garons|FR|
CCF|Carcassonne|Salvaza|FR|
PGF|Perpignan|Rivesaltes|FR|
PUF|Pau|Pyrénées|FR|
RNS|Rennes|Saint-Jacques|FR|
BES|Brest|Bretagne|FR|
*BRU|Brussels|Brussels Airport (Zaventem)|BE|בריסל,Bruxelles
*CRL|Brussels|Charleroi (Brussels South)|BE|Charleroi,בריסל
LGG|Liège|Liège|BE|
ANR|Antwerp|Antwerp|BE|
OST|Ostend|Ostend-Bruges|BE|Bruges
*AMS|Amsterdam|Schiphol|NL|אמסטרדם
EIN|Eindhoven|Eindhoven|NL|איינדהובן
RTM|Rotterdam|Rotterdam The Hague|NL|The Hague,רוטרדם
GRQ|Groningen|Eelde|NL|
MST|Maastricht|Maastricht Aachen|NL|
LUX|Luxembourg|Findel|LU|לוקסמבורג
*BER|Berlin|Brandenburg|DE|ברלין
*FRA|Frankfurt|Frankfurt am Main|DE|פרנקפורט
HHN|Frankfurt-Hahn|Hahn|DE|Hahn
*MUC|Munich|Franz Josef Strauss|DE|מינכן,München
FMM|Memmingen|Memmingen (Munich West)|DE|Munich
HAM|Hamburg|Hamburg|DE|המבורג
DUS|Düsseldorf|Düsseldorf|DE|דיסלדורף
NRN|Weeze|Weeze (Düsseldorf Weeze)|DE|Düsseldorf
CGN|Cologne|Cologne Bonn|DE|קלן,Köln,Bonn
STR|Stuttgart|Stuttgart|DE|שטוטגרט
HAJ|Hanover|Hannover|DE|Hannover
NUE|Nuremberg|Nürnberg|DE|Nürnberg
LEJ|Leipzig|Leipzig/Halle|DE|לייפציג
DRS|Dresden|Dresden|DE|דרזדן
BRE|Bremen|Bremen|DE|
DTM|Dortmund|Dortmund|DE|
FKB|Karlsruhe|Karlsruhe/Baden-Baden|DE|Baden-Baden
FDH|Friedrichshafen|Bodensee|DE|Lake Constance
FMO|Münster|Münster Osnabrück|DE|Osnabrück
PAD|Paderborn|Paderborn Lippstadt|DE|
SCN|Saarbrücken|Saarbrücken|DE|
*ZRH|Zürich|Zürich|CH|ציריך,Zurich
*GVA|Geneva|Geneva|CH|ז'נבה,Genève
BSL|Basel|EuroAirport Basel-Mulhouse-Freiburg|CH|באזל,Mulhouse
BRN|Bern|Bern|CH|
LUG|Lugano|Lugano|CH|
*VIE|Vienna|Vienna International|AT|וינה,Wien
SZG|Salzburg|W. A. Mozart|AT|זלצבורג
INN|Innsbruck|Innsbruck|AT|אינסברוק
GRZ|Graz|Graz|AT|
LNZ|Linz|Linz|AT|
KLU|Klagenfurt|Klagenfurt|AT|
*FCO|Rome|Fiumicino (Leonardo da Vinci)|IT|רומא,Roma
*CIA|Rome|Ciampino|IT|רומא,Roma
*MXP|Milan|Malpensa|IT|מילאנו,Milano
LIN|Milan|Linate|IT|מילאנו,Milano
*BGY|Milan|Bergamo (Orio al Serio)|IT|Bergamo,מילאנו
*VCE|Venice|Marco Polo|IT|ונציה,Venezia
TSF|Venice|Treviso|IT|Treviso,ונציה
*NAP|Naples|Capodichino|IT|נאפולי,Napoli,Amalfi
QSR|Salerno|Costa d'Amalfi|IT|Amalfi
*CTA|Catania|Fontanarossa|IT|קטניה,Sicily,סיציליה
*PMO|Palermo|Falcone-Borsellino|IT|פלרמו,Sicily,סיציליה
TPS|Trapani|Birgi|IT|Sicily
CIY|Comiso|Comiso|IT|Sicily
*BLQ|Bologna|Guglielmo Marconi|IT|בולוניה
*FLR|Florence|Peretola|IT|פירנצה,Firenze
*PSA|Pisa|Galileo Galilei|IT|פיזה,Florence
TRN|Turin|Caselle|IT|טורינו,Torino
GOA|Genoa|Cristoforo Colombo|IT|גנואה,Genova
VRN|Verona|Villafranca|IT|ורונה,Lake Garda
*BRI|Bari|Karol Wojtyła|IT|בארי,Puglia
BDS|Brindisi|Salento|IT|Puglia
CAG|Cagliari|Elmas|IT|Sardinia,סרדיניה
OLB|Olbia|Costa Smeralda|IT|Sardinia,סרדיניה
AHO|Alghero|Fertilia|IT|Sardinia
SUF|Lamezia Terme|Lamezia Terme|IT|Calabria
REG|Reggio Calabria|Tito Minniti|IT|
PSR|Pescara|Abruzzo|IT|
AOI|Ancona|Falconara|IT|
TRS|Trieste|Friuli Venezia Giulia|IT|
PEG|Perugia|San Francesco d'Assisi|IT|
RMI|Rimini|Federico Fellini|IT|
PMF|Parma|Parma|IT|
CUF|Cuneo|Levaldigi|IT|
*MAD|Madrid|Barajas|ES|מדריד
*BCN|Barcelona|El Prat|ES|ברצלונה
GRO|Girona|Girona-Costa Brava|ES|Barcelona
REU|Reus|Reus|ES|Barcelona,Tarragona
*AGP|Málaga|Costa del Sol|ES|מלגה,Marbella
ALC|Alicante|Alicante-Elche|ES|אליקנטה
*VLC|Valencia|Valencia|ES|ולנסיה
SVQ|Seville|San Pablo|ES|סביליה,Sevilla
*PMI|Palma de Mallorca|Palma de Mallorca|ES|Mallorca,Majorca,מיורקה
*IBZ|Ibiza|Ibiza|ES|איביזה
MAH|Menorca|Menorca (Mahón)|ES|Mahon
TFS|Tenerife|Tenerife South|ES|טנריף,Canary Islands
TFN|Tenerife|Tenerife North|ES|טנריף,Canary Islands
LPA|Gran Canaria|Gran Canaria|ES|Las Palmas,Canary Islands
ACE|Lanzarote|César Manrique-Lanzarote|ES|Canary Islands
FUE|Fuerteventura|Fuerteventura|ES|Canary Islands
SPC|La Palma|La Palma|ES|Canary Islands
BIO|Bilbao|Bilbao|ES|בילבאו
EAS|San Sebastián|San Sebastián|ES|Donostia
SCQ|Santiago de Compostela|Rosalía de Castro|ES|
VGO|Vigo|Peinador|ES|
OVD|Asturias|Asturias|ES|Oviedo
SDR|Santander|Seve Ballesteros|ES|
ZAZ|Zaragoza|Zaragoza|ES|
GRX|Granada|Federico García Lorca|ES|גרנדה
XRY|Jerez|Jerez|ES|
RMU|Murcia|Región de Murcia|ES|
LEI|Almería|Almería|ES|
*LIS|Lisbon|Humberto Delgado|PT|ליסבון,Lisboa
*OPO|Porto|Francisco Sá Carneiro|PT|פורטו,Oporto
FAO|Faro|Faro|PT|פארו,Algarve
FNC|Funchal|Madeira|PT|Madeira,מדיירה
PXO|Porto Santo|Porto Santo|PT|Madeira
PDL|Ponta Delgada|João Paulo II|PT|Azores,האיים האזוריים
TER|Terceira|Lajes|PT|Azores
*MLA|Malta|Malta International|MT|מלטה,Valletta
GIB|Gibraltar|Gibraltar|GI|
*CPH|Copenhagen|Kastrup|DK|קופנהגן,København
BLL|Billund|Billund|DK|Legoland
AAL|Aalborg|Aalborg|DK|
AAR|Aarhus|Aarhus|DK|
*ARN|Stockholm|Arlanda|SE|שטוקהולם
BMA|Stockholm|Bromma|SE|שטוקהולם
NYO|Stockholm|Skavsta|SE|Nyköping
VST|Stockholm|Västerås|SE|Västerås
GOT|Gothenburg|Landvetter|SE|Göteborg,גטבורג
MMX|Malmö|Malmö|SE|
*OSL|Oslo|Gardermoen|NO|אוסלו
TRF|Oslo|Torp Sandefjord|NO|Sandefjord,Torp
BGO|Bergen|Flesland|NO|ברגן
TRD|Trondheim|Værnes|NO|
SVG|Stavanger|Sola|NO|
TOS|Tromsø|Tromsø|NO|טרומסו,Northern lights
BOO|Bodø|Bodø|NO|
*HEL|Helsinki|Helsinki-Vantaa|FI|הלסינקי
RVN|Rovaniemi|Rovaniemi|FI|רובניימי,Lapland,לפלנד
KTT|Kittilä|Kittilä|FI|Lapland,Levi
OUL|Oulu|Oulu|FI|
TMP|Tampere|Tampere-Pirkkala|FI|
TKU|Turku|Turku|FI|
KEF|Reykjavik|Keflavík|IS|רייקיאוויק,Iceland
RKV|Reykjavik|Reykjavík Domestic|IS|
*VNO|Vilnius|Vilnius|LT|וילנה
KUN|Kaunas|Kaunas|LT|קובנה
PLQ|Palanga|Palanga|LT|
*RIX|Riga|Riga|LV|ריגה
*TLL|Tallinn|Lennart Meri|EE|טאלין
*WAW|Warsaw|Chopin|PL|ורשה,Warszawa
WMI|Warsaw|Modlin|PL|ורשה,Modlin
*KRK|Kraków|John Paul II|PL|קרקוב,Krakow,Cracow
KTW|Katowice|Katowice|PL|קטוביץ
GDN|Gdańsk|Lech Wałęsa|PL|גדנסק
WRO|Wrocław|Copernicus|PL|ורוצלב
POZ|Poznań|Ławica|PL|פוזנן
LCJ|Łódź|Władysław Reymont|PL|לודז
RZE|Rzeszów|Jasionka|PL|
SZZ|Szczecin|Goleniów|PL|
LUZ|Lublin|Lublin|PL|
BZG|Bydgoszcz|Ignacy Jan Paderewski|PL|
*PRG|Prague|Václav Havel|CZ|פראג,Praha
BRQ|Brno|Tuřany|CZ|ברנו
OSR|Ostrava|Leoš Janáček|CZ|
*BTS|Bratislava|M. R. Štefánik|SK|ברטיסלבה
KSC|Košice|Košice|SK|
*BUD|Budapest|Ferenc Liszt|HU|בודפשט
DEB|Debrecen|Debrecen|HU|
*OTP|Bucharest|Henri Coandă|RO|בוקרשט,Otopeni
CLJ|Cluj-Napoca|Avram Iancu|RO|קלוז',Cluj
IAS|Iași|Iași|RO|יאשי
TSR|Timișoara|Traian Vuia|RO|
SBZ|Sibiu|Sibiu|RO|
CND|Constanța|Mihail Kogălniceanu|RO|
SCV|Suceava|Ștefan cel Mare|RO|
CRA|Craiova|Craiova|RO|
OMR|Oradea|Oradea|RO|
BCM|Bacău|George Enescu|RO|
TGM|Târgu Mureș|Transilvania|RO|
RMO|Chișinău|Chișinău|MD|קישינב,Kishinev
*SOF|Sofia|Vasil Levski|BG|סופיה
*VAR|Varna|Varna|BG|ורנה
*BOJ|Burgas|Burgas|BG|בורגס,Sunny Beach
PDV|Plovdiv|Plovdiv|BG|פלובדיב
*BEG|Belgrade|Nikola Tesla|RS|בלגרד,Beograd
INI|Niš|Constantine the Great|RS|
*TGD|Podgorica|Podgorica|ME|פודגוריצה
*TIV|Tivat|Tivat|ME|טיבט,Kotor,Budva
*ZAG|Zagreb|Franjo Tuđman|HR|זאגרב
*DBV|Dubrovnik|Čilipi|HR|דוברובניק
*SPU|Split|Split|HR|ספליט
ZAD|Zadar|Zadar|HR|
PUY|Pula|Pula|HR|Istria
RJK|Rijeka|Krk|HR|
OSI|Osijek|Osijek|HR|
BWK|Brač|Bol|HR|
*LJU|Ljubljana|Jože Pučnik|SI|ליובליאנה,Lake Bled
SJJ|Sarajevo|Sarajevo|BA|סרייבו
TZL|Tuzla|Tuzla|BA|
BNX|Banja Luka|Banja Luka|BA|
OMO|Mostar|Mostar|BA|
SKP|Skopje|Skopje|MK|סקופיה
OHD|Ohrid|St. Paul the Apostle|MK|אוחריד
*TIA|Tirana|Nënë Tereza|AL|טירנה
PRN|Pristina|Adem Jashari|XK|פרישטינה
*TBS|Tbilisi|Shota Rustaveli|GE|טביליסי
*BUS|Batumi|Batumi|GE|בטומי
*KUT|Kutaisi|David the Builder|GE|קוטאיסי
*EVN|Yerevan|Zvartnots|AM|ירוואן
LWN|Gyumri|Shirak|AM|
*GYD|Baku|Heydar Aliyev|AZ|באקו
SVO|Moscow|Sheremetyevo|RU|מוסקבה
DME|Moscow|Domodedovo|RU|מוסקבה
VKO|Moscow|Vnukovo|RU|מוסקבה
LED|Saint Petersburg|Pulkovo|RU|סנט פטרסבורג,St Petersburg
MSQ|Minsk|Minsk National|BY|מינסק
TAS|Tashkent|Islam Karimov|UZ|טשקנט
SKD|Samarkand|Samarkand|UZ|סמרקנד
ALA|Almaty|Almaty|KZ|אלמטי
NQZ|Astana|Nursultan Nazarbayev|KZ|אסטנה
FRU|Bishkek|Manas|KG|בישקק
*DXB|Dubai|Dubai International|AE|דובאי
DWC|Dubai|Al Maktoum|AE|דובאי,Dubai World Central
*AUH|Abu Dhabi|Zayed International|AE|אבו דאבי
SHJ|Sharjah|Sharjah|AE|
RKT|Ras Al Khaimah|Ras Al Khaimah|AE|
BAH|Bahrain|Bahrain International|BH|בחריין,Manama
DOH|Doha|Hamad|QA|דוחא
MCT|Muscat|Muscat|OM|מוסקט
SLL|Salalah|Salalah|OM|
RUH|Riyadh|King Khalid|SA|ריאד
JED|Jeddah|King Abdulaziz|SA|ג'דה
DMM|Dammam|King Fahd|SA|
MED|Medina|Prince Mohammad bin Abdulaziz|SA|
ULH|AlUla|AlUla|SA|
KWI|Kuwait City|Kuwait International|KW|
AMM|Amman|Queen Alia|JO|עמאן
AQJ|Aqaba|King Hussein|JO|עקבה,Petra
CAI|Cairo|Cairo International|EG|קהיר
SPX|Cairo|Sphinx (Giza)|EG|Giza
*SSH|Sharm el-Sheikh|Sharm el-Sheikh|EG|שארם,Sinai,סיני
HRG|Hurghada|Hurghada|EG|הורגדה
RMF|Marsa Alam|Marsa Alam|EG|
LXR|Luxor|Luxor|EG|לוקסור
TCP|Taba|Taba|EG|טאבה,Sinai
HBE|Alexandria|Borg El Arab|EG|אלכסנדריה
*RAK|Marrakech|Menara|MA|מרקש,Marrakesh
CMN|Casablanca|Mohammed V|MA|קזבלנקה
RBA|Rabat|Rabat-Salé|MA|
FEZ|Fes|Fès-Saïss|MA|פאס,Fez
TNG|Tangier|Ibn Battouta|MA|טנג'יר
AGA|Agadir|Al Massira|MA|אגדיר
ESU|Essaouira|Mogador|MA|
TUN|Tunis|Carthage|TN|תוניס
DJE|Djerba|Djerba-Zarzis|TN|ג'רבה
MIR|Monastir|Habib Bourguiba|TN|
NBE|Enfidha|Enfidha-Hammamet|TN|Hammamet
JNB|Johannesburg|O. R. Tambo|ZA|יוהנסבורג
CPT|Cape Town|Cape Town|ZA|קייפטאון
DUR|Durban|King Shaka|ZA|
NBO|Nairobi|Jomo Kenyatta|KE|ניירובי
MBA|Mombasa|Moi|KE|
ADD|Addis Ababa|Bole|ET|אדיס אבבה
ZNZ|Zanzibar|Abeid Amani Karume|TZ|זנזיבר
DAR|Dar es Salaam|Julius Nyerere|TZ|
JRO|Kilimanjaro|Kilimanjaro|TZ|קילימנג'רו,Arusha
MRU|Mauritius|Sir Seewoosagur Ramgoolam|MU|מאוריציוס
SEZ|Mahé|Seychelles International|SC|סיישל,Seychelles
RUN|Saint-Denis|Roland Garros|RE|Réunion
LOS|Lagos|Murtala Muhammed|NG|
ACC|Accra|Kotoka|GH|
KGL|Kigali|Kigali|RW|
EBB|Entebbe|Entebbe|UG|אנטבה,Kampala
WDH|Windhoek|Hosea Kutako|NA|
VFA|Victoria Falls|Victoria Falls|ZW|
TNR|Antananarivo|Ivato|MG|
NOS|Nosy Be|Fascene|MG|
DSS|Dakar|Blaise Diagne|SN|
*DEL|Delhi|Indira Gandhi|IN|דלהי,New Delhi
*BOM|Mumbai|Chhatrapati Shivaji Maharaj|IN|מומבאי,Bombay
*GOI|Goa|Dabolim|IN|גואה
GOX|Goa|Manohar (Mopa)|IN|גואה
BLR|Bengaluru|Kempegowda|IN|Bangalore
MAA|Chennai|Chennai|IN|Madras
COK|Kochi|Cochin|IN|Kerala
CCU|Kolkata|Netaji Subhas Chandra Bose|IN|Calcutta
HYD|Hyderabad|Rajiv Gandhi|IN|
JAI|Jaipur|Jaipur|IN|
CMB|Colombo|Bandaranaike|LK|קולומבו,Sri Lanka,סרי לנקה
MLE|Malé|Velana|MV|Maldives,המלדיביים
KTM|Kathmandu|Tribhuvan|NP|קטמנדו
*BKK|Bangkok|Suvarnabhumi|TH|בנגקוק
DMK|Bangkok|Don Mueang|TH|בנגקוק
*HKT|Phuket|Phuket|TH|פוקט
CNX|Chiang Mai|Chiang Mai|TH|צ'יאנג מאי
USM|Koh Samui|Samui|TH|קוסמוי,Samui
KBV|Krabi|Krabi|TH|קראבי
SGN|Ho Chi Minh City|Tan Son Nhat|VN|Saigon,הו צ'י מין
HAN|Hanoi|Noi Bai|VN|האנוי
DAD|Da Nang|Da Nang|VN|
PQC|Phu Quoc|Phu Quoc|VN|
CXR|Nha Trang|Cam Ranh|VN|
KTI|Phnom Penh|Techo|KH|פנום פן
SAI|Siem Reap|Siem Reap-Angkor|KH|Angkor
VTE|Vientiane|Wattay|LA|
RGN|Yangon|Yangon|MM|Rangoon
KUL|Kuala Lumpur|Kuala Lumpur International|MY|קואלה לומפור
PEN|Penang|Penang|MY|
LGK|Langkawi|Langkawi|MY|
BKI|Kota Kinabalu|Kota Kinabalu|MY|Borneo
SIN|Singapore|Changi|SG|סינגפור
CGK|Jakarta|Soekarno-Hatta|ID|ג'קרטה
DPS|Bali|Ngurah Rai (Denpasar)|ID|באלי,Denpasar
MNL|Manila|Ninoy Aquino|PH|מנילה
CEB|Cebu|Mactan-Cebu|PH|
HKG|Hong Kong|Hong Kong International|HK|הונג קונג
MFM|Macau|Macau International|MO|
TPE|Taipei|Taoyuan|TW|טאיפיי
TSA|Taipei|Songshan|TW|טאיפיי
PEK|Beijing|Capital|CN|בייג'ינג,Peking
PKX|Beijing|Daxing|CN|בייג'ינג
PVG|Shanghai|Pudong|CN|שנגחאי
SHA|Shanghai|Hongqiao|CN|שנגחאי
CAN|Guangzhou|Baiyun|CN|Canton
SZX|Shenzhen|Bao'an|CN|
TFU|Chengdu|Tianfu|CN|
CTU|Chengdu|Shuangliu|CN|
CKG|Chongqing|Jiangbei|CN|
XIY|Xi'an|Xianyang|CN|
HGH|Hangzhou|Xiaoshan|CN|
KMG|Kunming|Changshui|CN|
*NRT|Tokyo|Narita|JP|טוקיו
*HND|Tokyo|Haneda|JP|טוקיו
KIX|Osaka|Kansai|JP|אוסקה,Kyoto
ITM|Osaka|Itami|JP|אוסקה
UKB|Kobe|Kobe|JP|Osaka
NGO|Nagoya|Chubu Centrair|JP|
CTS|Sapporo|New Chitose|JP|Hokkaido
FUK|Fukuoka|Fukuoka|JP|
OKA|Okinawa|Naha|JP|Naha
ICN|Seoul|Incheon|KR|סיאול
GMP|Seoul|Gimpo|KR|סיאול
PUS|Busan|Gimhae|KR|
CJU|Jeju|Jeju|KR|
UBN|Ulaanbaatar|Chinggis Khaan|MN|
SYD|Sydney|Kingsford Smith|AU|סידני
MEL|Melbourne|Tullamarine|AU|מלבורן
BNE|Brisbane|Brisbane|AU|
PER|Perth|Perth|AU|
ADL|Adelaide|Adelaide|AU|
AKL|Auckland|Auckland|NZ|אוקלנד
CHC|Christchurch|Christchurch|NZ|
ZQN|Queenstown|Queenstown|NZ|
NAN|Nadi|Nadi|FJ|Fiji
PPT|Papeete|Faa'a|PF|Tahiti,Bora Bora
*JFK|New York|John F. Kennedy|US|ניו יורק,NYC
*EWR|New York|Newark Liberty|US|Newark,ניו יורק
LGA|New York|LaGuardia|US|ניו יורק
*LAX|Los Angeles|Los Angeles International|US|לוס אנג'לס,LA
*SFO|San Francisco|San Francisco International|US|סן פרנסיסקו
OAK|Oakland|Oakland|US|San Francisco
SJC|San Jose|Mineta San José|US|Silicon Valley
*MIA|Miami|Miami International|US|מיאמי
FLL|Fort Lauderdale|Fort Lauderdale-Hollywood|US|Miami
MCO|Orlando|Orlando International|US|אורלנדו,Disney World
TPA|Tampa|Tampa|US|
*BOS|Boston|Logan|US|בוסטון
IAD|Washington|Dulles|US|וושינגטון
DCA|Washington|Ronald Reagan National|US|וושינגטון
BWI|Baltimore|Baltimore/Washington|US|Washington
ORD|Chicago|O'Hare|US|שיקגו
MDW|Chicago|Midway|US|שיקגו
ATL|Atlanta|Hartsfield-Jackson|US|אטלנטה
DFW|Dallas|Dallas/Fort Worth|US|דאלאס
IAH|Houston|George Bush Intercontinental|US|יוסטון
HOU|Houston|Hobby|US|יוסטון
DEN|Denver|Denver|US|דנבר
SEA|Seattle|Seattle-Tacoma|US|סיאטל
LAS|Las Vegas|Harry Reid|US|לאס וגאס
PHX|Phoenix|Sky Harbor|US|
PHL|Philadelphia|Philadelphia|US|פילדלפיה
DTW|Detroit|Detroit Metropolitan|US|
MSP|Minneapolis|Minneapolis-Saint Paul|US|
CLT|Charlotte|Charlotte Douglas|US|
SAN|San Diego|San Diego|US|סן דייגו
AUS|Austin|Austin-Bergstrom|US|
SLC|Salt Lake City|Salt Lake City|US|
PDX|Portland|Portland|US|
HNL|Honolulu|Daniel K. Inouye|US|הונולולו,Hawaii,הוואי
OGG|Maui|Kahului|US|Hawaii
ANC|Anchorage|Ted Stevens|US|Alaska
MSY|New Orleans|Louis Armstrong|US|ניו אורלינס
BNA|Nashville|Nashville|US|
RDU|Raleigh|Raleigh-Durham|US|
PIT|Pittsburgh|Pittsburgh|US|
STL|St. Louis|St. Louis Lambert|US|
SAT|San Antonio|San Antonio|US|
SJU|San Juan|Luis Muñoz Marín|PR|
YYZ|Toronto|Pearson|CA|טורונטו
YTZ|Toronto|Billy Bishop|CA|טורונטו
YUL|Montreal|Trudeau|CA|מונטריאול,Montréal
YVR|Vancouver|Vancouver|CA|ונקובר
YYC|Calgary|Calgary|CA|
YEG|Edmonton|Edmonton|CA|
YOW|Ottawa|Macdonald-Cartier|CA|אוטווה
YQB|Quebec City|Jean Lesage|CA|Québec
YHZ|Halifax|Stanfield|CA|
YWG|Winnipeg|Richardson|CA|
MEX|Mexico City|Benito Juárez|MX|מקסיקו סיטי
NLU|Mexico City|Felipe Ángeles|MX|
CUN|Cancún|Cancún|MX|קנקון,Cancun
TQO|Tulum|Felipe Carrillo Puerto|MX|טולום
PVR|Puerto Vallarta|Gustavo Díaz Ordaz|MX|
SJD|Los Cabos|Los Cabos|MX|Cabo
GDL|Guadalajara|Miguel Hidalgo|MX|
PUJ|Punta Cana|Punta Cana|DO|פונטה קאנה
SDQ|Santo Domingo|Las Américas|DO|
HAV|Havana|José Martí|CU|הוואנה
MBJ|Montego Bay|Sangster|JM|
NAS|Nassau|Lynden Pindling|BS|Bahamas
AUA|Oranjestad|Queen Beatrix|AW|Aruba
CUR|Willemstad|Hato|CW|Curaçao
BGI|Bridgetown|Grantley Adams|BB|Barbados
SXM|Philipsburg|Princess Juliana|SX|St Maarten,Saint Martin
PTY|Panama City|Tocumen|PA|פנמה
SJO|San José|Juan Santamaría|CR|Costa Rica,קוסטה ריקה
LIR|Liberia|Guanacaste|CR|Costa Rica
GUA|Guatemala City|La Aurora|GT|
SAL|San Salvador|El Salvador International|SV|
BOG|Bogotá|El Dorado|CO|בוגוטה
MDE|Medellín|José María Córdova|CO|מדיין
CTG|Cartagena|Rafael Núñez|CO|קרטחנה
LIM|Lima|Jorge Chávez|PE|לימה
CUZ|Cusco|Alejandro Velasco Astete|PE|קוסקו,Machu Picchu
SCL|Santiago|Arturo Merino Benítez|CL|סנטיאגו
EZE|Buenos Aires|Ezeiza|AR|בואנוס איירס
AEP|Buenos Aires|Aeroparque Jorge Newbery|AR|בואנוס איירס
IGR|Puerto Iguazú|Cataratas del Iguazú|AR|Iguazu
FTE|El Calafate|El Calafate|AR|Patagonia
USH|Ushuaia|Malvinas Argentinas|AR|Patagonia
BRC|Bariloche|Teniente Luis Candelaria|AR|Patagonia
GRU|São Paulo|Guarulhos|BR|סאו פאולו
CGH|São Paulo|Congonhas|BR|סאו פאולו
VCP|São Paulo|Viracopos (Campinas)|BR|Campinas
GIG|Rio de Janeiro|Galeão|BR|ריו דה ז'ניירו,Rio
SDU|Rio de Janeiro|Santos Dumont|BR|ריו דה ז'ניירו,Rio
IGU|Foz do Iguaçu|Cataratas|BR|Iguazu
SSA|Salvador|Salvador Bahia|BR|
REC|Recife|Guararapes|BR|
FLN|Florianópolis|Hercílio Luz|BR|
BSB|Brasília|Presidente Juscelino Kubitschek|BR|
UIO|Quito|Mariscal Sucre|EC|קיטו
GYE|Guayaquil|José Joaquín de Olmedo|EC|
GPS|Galápagos|Seymour (Baltra)|EC|Galapagos
MVD|Montevideo|Carrasco|UY|מונטווידאו
ASU|Asunción|Silvio Pettirossi|PY|
VVI|Santa Cruz|Viru Viru|BO|
LPB|La Paz|El Alto|BO|
CCS|Caracas|Simón Bolívar|VE|
`;

export const METROS = `
LON|London|GB|LHR LGW STN LTN LCY SEN|לונדון
PAR|Paris|FR|CDG ORY BVA|פריז
ROM|Rome|IT|FCO CIA|רומא,Roma
MIL|Milan|IT|MXP LIN BGY|מילאנו,Milano
IST/SAW|Istanbul|TR|IST SAW|איסטנבול
BRU/CRL|Brussels|BE|BRU CRL|בריסל
VCE/TSF|Venice|IT|VCE TSF|ונציה
STO|Stockholm|SE|ARN BMA NYO VST|שטוקהולם
OSL/TRF|Oslo|NO|OSL TRF|אוסלו
WAW/WMI|Warsaw|PL|WAW WMI|ורשה
REK|Reykjavik|IS|KEF RKV|רייקיאוויק
TFS/TFN|Tenerife|ES|TFS TFN|טנריף
BFS/BHD|Belfast|GB|BFS BHD|בלפסט
MOW|Moscow|RU|SVO DME VKO|מוסקבה
DXB/DWC|Dubai|AE|DXB DWC|דובאי
BKK/DMK|Bangkok|TH|BKK DMK|בנגקוק
NYC|New York|US|JFK EWR LGA|ניו יורק
WAS|Washington|US|IAD DCA BWI|וושינגטון
CHI|Chicago|US|ORD MDW|שיקגו
YTO|Toronto|CA|YYZ YTZ|טורונטו
TYO|Tokyo|JP|HND NRT|טוקיו
OSA|Osaka|JP|KIX ITM UKB|אוסקה
SEL|Seoul|KR|ICN GMP|סיאול
BJS|Beijing|CN|PEK PKX|בייג'ינג
PVG/SHA|Shanghai|CN|PVG SHA|שנגחאי
TPE/TSA|Taipei|TW|TPE TSA|טאיפיי
BUE|Buenos Aires|AR|EZE AEP|בואנוס איירס
SAO|São Paulo|BR|GRU CGH VCP|סאו פאולו
RIO|Rio de Janeiro|BR|GIG SDU|ריו דה ז'ניירו,Rio
`;

// Region names the agent understands as explore searches (see is_flexible in backend/app/agent.py).
export const REGIONS = `
Anywhere|Cheapest places the agent can find|anywhere,everywhere,כל מקום,לכל מקום
Europe|Region — the agent compares several cities|אירופה
Eastern Europe|Region — the agent compares several cities|מזרח אירופה
Balkans|Region — Serbia, Montenegro, Albania, Croatia…|הבלקן,בלקן
Greek islands|Region — Crete, Rhodes, Corfu, Cyclades…|איי יוון,Greek isles
Scandinavia|Region — Denmark, Sweden, Norway|סקנדינביה,Nordics
Mediterranean|Region — beach destinations around the Med|הים התיכון
Canary Islands|Region — Tenerife, Gran Canaria, Lanzarote…|האיים הקנריים,Canaries
Caribbean|Region|הקריביים
Southeast Asia|Region — Thailand, Vietnam, Bali…|דרום מזרח אסיה
Asia|Region|אסיה
North America|Region|צפון אמריקה
South America|Region|דרום אמריקה
Africa|Region|אפריקה
`;
