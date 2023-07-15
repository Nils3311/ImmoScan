import json
import logging
import re
import requests
import sqlite3
import time
import hashlib
from dotenv import load_dotenv
import os
from uuid import uuid4

from bs4 import BeautifulSoup
from telegram import Update, InputMediaPhoto, InlineKeyboardButton, \
    InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ForceReply, LabeledPrice
from telegram.ext import CallbackContext, CommandHandler, Updater, MessageHandler, Filters, CallbackQueryHandler, \
    ConversationHandler, PreCheckoutQueryHandler

load_dotenv()
token = os.getenv('TOKEN')
update = '- Fehlerbehebung bei mehreren Bildern bei Gruppenbenachrichtigungen\n\n'

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)


### Type 1: Immonet
### Type 2: Immoscout24
def init_db():
    # create database if not exists
    db_file = './db/database_bot.db'
    os.makedirs(os.path.dirname(db_file), exist_ok=True)
    db = sqlite3.connect(db_file, check_same_thread=False)
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS wohnungen
            (
                wohnungs_id integer,
                portal      integer,
                chat_id     integer,
                pk          integer
                    constraint wohnungen_pk
                        primary key autoincrement
            );
            '''
    )
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS jobs
            (
                chat_id      integer,
                beschreibung text,
                url          text,
                type         integer,
                pk           integer
                    constraint jobs_pk
                        primary key autoincrement,
                hash         text
            );
            '''
    )
    db.execute(
        '''
        CREATE TABLE IF NOT EXISTS users
        (
            pk         integer
                constraint users_pk
                    primary key autoincrement,
            user_id    integer,
            name       text,
            first_name text,
            last_name  text,
            paid       integer,
            paid_date  DATE
        );
        '''
    )
    print('Datenbank initiiert')
    return db


# Helper
def clean_message(message):
    message = message.replace('-', '\\-')
    message = message.replace('!', '\\!')
    message = message.replace('.', '\\.')
    message = message.replace('#', '\\#')
    message = message.replace('(', '\\(')
    message = message.replace(')', '\\)')
    message = message.replace('=', '\\=')
    message = message.replace('+', '\\+')
    message = message.replace('|', '\\|')
    message = message.replace('~', '')
    return message


def clean_markup(message):
    return message.replace("_", "\\_").replace("*", "\\*")


def clean_html(text):
    return text.replace('\t', '').replace('\n', '')


def url_ok(image_url):
    r = requests.head(image_url)
    return r.status_code == 200


def get_user(update, create_user=True):
    user = update.effective_user
    db_user = db.execute(f"SELECT * FROM users WHERE user_id = {user.id}").fetchone()
    if db_user is None and create_user:
        db.execute(
            f"INSERT INTO users (user_id, name, first_name, last_name) VALUES ({user.id}, '{user.name}', '{user.first_name}', '{user.last_name}')")
        db.commit()
        db_user = db.execute(f"SELECT * FROM users WHERE pk = {user.id}").fetchone()
    if db_user is not None:
        return db_user
    else:
        return None


def immonet_search(context, pk):
    base_url = 'https://www.immonet.de/'
    job = context.job
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:82.0) Gecko/20100101 Firefox/82.0',
        'Accept-Language': 'en-US,en;q=0.5'
    }
    db_job = db.execute(f"SELECT * FROM jobs WHERE pk = {pk}").fetchone()
    db_hash = db_job[5]
    description = db_job[1]
    url = db_job[2]
    base_soup = BeautifulSoup(requests.get(url, headers=headers).content, 'html.parser')
    results = base_soup.find(id="result-list-stage")
    offers = results.find_all("div", {"id": re.compile('selObject_*')})
    for offer in offers:
        id = offer.attrs['id'].split('_')[1]
        exists = db.execute(
            f"SELECT * FROM wohnungen WHERE wohnungs_id = {id}").fetchone()
        if exists is None:
            if db_hash is not None:
                title = offer.find(class_='text-225').attrs['title']
                url = offer.find(class_='text-225').attrs['href']
                sub_soup = BeautifulSoup(requests.get(base_url + url, headers=headers).content, 'html.parser')
                adresse = clean_html(sub_soup.find(class_='mini-map-icon-svg').parent.p.getText()).replace(
                    'Auf Karte anzeigen',
                    '')
                preisdaten = sub_soup.find(id='panelPrices').find_all(class_='row list-100')
                preisdaten_tabelle = ""
                for preis in preisdaten:
                    if preis.text != "":
                        preisdaten_tabelle += clean_html(preis.find_all('div')[0].getText()) + ": " + clean_html(
                            preis.find_all('div')[1].getText()) + '\n'
                context.bot.send_message(chat_id=job.context, text=clean_message(
                    f'[*{clean_markup(title)}*]({clean_markup(base_url + url)})\n'
                    f'{adresse}\n\n'
                    f'{preisdaten_tabelle}\n'
                    f'Anbieter: Immowelt\n'
                    f'Suche: {description}'),
                                         parse_mode='MarkdownV2'
                                         )
                db.execute(f"INSERT INTO wohnungen (wohnungs_id, portal, chat_id) VALUES ('{id}','1', {int(job.name)})")
                db.commit()
                images = sub_soup.find(class_='fotorama')
                if images is not None:
                    images = images.find_all('div')
                    media_group = []
                    for image in images:
                        media_group.append(InputMediaPhoto(image.attrs['data-img']))
                        if len(media_group) >= 10:
                            context.bot.send_media_group(chat_id=job.context, media=media_group)
                            media_group = []
                            if context.bot.get_chat(job.context).type == 'group':
                                time.sleep(45)
                    if len(media_group) >= 5 and context.bot.get_chat(job.context).type == 'group':
                        context.bot.send_media_group(chat_id=job.context, media=media_group)
                        time.sleep(45)
    if db_hash is None:
        db.execute(f"UPDATE jobs SET hash = '1' WHERE pk = {pk};")
        db.commit()


def immoscout24_search(context, pk):
    job = context.job
    random_cookie = str(uuid4())
    headers = {
        'Cookie': 'reese84={}'.format(random_cookie)
    }
    db_job = db.execute(f"SELECT * FROM jobs WHERE pk = {pk}").fetchone()
    db_hash = db_job[5]
    description = db_job[1]
    url = db_job[2]
    base_json = json.loads(requests.post(url, headers=headers).content)
    offers = base_json['searchResponseModel']['resultlist.resultlist']['resultlistEntries'][0]['resultlistEntry']
    url = db_job[2]
    for offer in offers:
        id = offer['@id']
        # Check if not first Time run
        exists = db.execute(f"SELECT * FROM wohnungen WHERE wohnungs_id = {id}").fetchone()
        if exists is None:
            if db_hash is not None:
                title = offer['resultlist.realEstate']['title']
                url = f"https://www.immobilienscout24.de/expose/{id}"
                adresse = offer['resultlist.realEstate']['address']['description']['text']
                daten_tabelle = ""
                for attr in offer['attributes'][0]['attribute']:
                    daten_tabelle += str(attr['label']) + ": " + str(attr['value']) + '\n'
                context.bot.send_message(chat_id=job.context, text=clean_message(
                    f'[*{clean_markup(title)}*]({clean_markup(url)})\n'
                    f'{adresse}\n\n'
                    f'{daten_tabelle}\n'
                    f'Anbieter: Immobilienscout24\n'
                    f'Suche: {description}'),
                                         parse_mode='MarkdownV2'
                                         )
                db.execute(f"INSERT INTO wohnungen (wohnungs_id, portal, chat_id) VALUES ('{id}','2', {int(job.name)})")
                db.commit()
                media_group = []
                images = offer['resultlist.realEstate']['galleryAttachments']['attachment']
                if type(images) is list:
                    for image in images:
                        if 'urls' in image.keys():
                            img_url = image['urls'][0]['url']['@href'].split('legacy_thumbnail', 1)[
                                          0] + 'resize/500x500/format/jpg/quality/50'
                            if url_ok(img_url):
                                media_group.append(InputMediaPhoto(img_url))
                            if len(media_group) >= 10:
                                context.bot.send_media_group(chat_id=job.context, media=media_group)
                                media_group = []
                                if context.bot.get_chat(job.context).type == 'group':
                                    time.sleep(45)
                else:
                    if 'urls' in images.keys():
                        img_url = images['urls'][0]['url']['@href'].split('legacy_thumbnail', 1)[
                                      0] + 'resize/500x500/format/jpg/quality/50'
                        media_group.append(InputMediaPhoto(img_url))
                if len(media_group) >= 1:
                    context.bot.send_media_group(chat_id=job.context, media=media_group)
                    if len(media_group) >= 5 and context.bot.get_chat(job.context).type == 'group':
                        time.sleep(45)
    if db_hash is None:
        db.execute(f"UPDATE jobs SET hash = '1' WHERE pk = {pk};")
        db.commit()


def other_search(context, pk):
    job = context.job
    db_job = db.execute(f"SELECT * FROM jobs WHERE pk = {pk}").fetchone()
    db_hash = db_job[5]
    db_website = db_job[2]
    db_description = db_job[1]
    url = db_job[2]
    # Get Website and Clean
    soup = BeautifulSoup(requests.get(url).content, 'html.parser')
    [s.extract() for s in soup(['style', 'script', '[document]', 'head', 'title'])]
    visible_text = soup.getText().replace("\n", "").encode('utf-8')
    currentHash = hashlib.sha224(visible_text).hexdigest()
    if db_hash == "" or db_hash is None:
        db.execute(f"UPDATE jobs SET hash = '{currentHash}' WHERE pk = {pk};")
        db.commit()
    elif db_hash != currentHash:
        context.bot.send_message(chat_id=job.context,
                                 text=clean_message(
                                     f'Es gab eine Änderung auf einer Webseite:\n'
                                     f'{clean_markup(db_description)}: {clean_markup(db_website)}'),
                                 parse_mode='MarkdownV2'
                                 )
        db.execute(f"UPDATE jobs SET hash = '{currentHash}' WHERE pk = {pk};")
        db.commit()
    elif db_hash == currentHash:
        pass
    else:
        context.bot.send_message(chat_id=job.context,
                                 text=clean_message(
                                     f'Im Webseitenvergleich von {db_description} liegt ein unbekannter Fehler vor'))


def job_exists(chat_id):
    searches = db.execute(f"SELECT * FROM jobs WHERE chat_id = {int(chat_id)}").fetchone()
    if not searches:
        return False
    else:
        return True


def search_isrunning(context, chat_id):
    current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    if not current_jobs:
        return False
    else:
        return True


def searchdata_exists(chat_id):
    exists = db.execute(f"SELECT * FROM wohnungen WHERE chat_id = {chat_id}").fetchone()
    if exists:
        return True
    else:
        return False


def buttons_mainmenu(context, chat_id):
    buttons = []
    if job_exists(chat_id):
        if not search_isrunning(context, chat_id):
            buttons.append([InlineKeyboardButton("🔎 Suche starten", callback_data='/suche_starten')])
        else:
            buttons.append([InlineKeyboardButton("✋ Suche stoppen", callback_data='/suche_stoppen')])
    buttons.append([InlineKeyboardButton("🆕 Eine neue Suche erstellen", callback_data='/suche_erstellen')])
    if job_exists(chat_id):
        buttons.append([InlineKeyboardButton("✏️ Suchen bearbeiten", callback_data='/suche_bearbeiten')])
    buttons.append([InlineKeyboardButton("☕️ Unterstütze mich", callback_data='/pay')])
    buttons.append([InlineKeyboardButton("❔ Hilfe", callback_data='/help')])
    return buttons


back = [[InlineKeyboardButton("⬅️ zurück", callback_data='/main_menu')]]

PORTAL, URL_IMMONET, URL_IMMOSCOUT24, URL_OTHER, DESCRIPTION = range(5)


def start_search_func(update, context, chat_id):
    if search_isrunning(context, chat_id):
        text = 'Deine Suche läuft bereits.'
    else:
        searches = db.execute(f"SELECT * FROM jobs WHERE chat_id = {chat_id}").fetchall()
        if not searches:
            text = 'Es ist keine Suche eingerichtet. Gehe ins Hauptmenü und klicke auf "Neue Suche erstellen"'
        else:
            context.job_queue.run_repeating(search_results, 60, 5, context=chat_id, name=str(chat_id))
            searchlist = [f"• *{search[1]}*\n" for search in searches]
            text = clean_message(
                f'Die Suche wurde gestartet. Sobald es neue Ergebnisse gibt, wirst du benachrichtigt.\n\n'
                f'Liste:\n'
                f'{"".join(searchlist)}')
        update.callback_query.edit_message_text(
            text=text,
            reply_markup=(InlineKeyboardMarkup(back)),
            parse_mode='MarkdownV2')


### Telegram Bot
# Start Dialog
def start(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    # Sollte nun Klappen!
    # if update.effective_chat.type == 'group':
    #     context.bot.send_message(chat_id=chat_id,
    #                              text=clean_message(
    #                                  "*Hinweis: Aufgrund von Spam-Beschränkungen von Telegram werden in Gruppenchats leider häufig nicht alle Bilder versendet. Falls dies geschieht, erhältst du einen Hinweis.*"),
    #                              parse_mode='MarkdownV2')
    get_user(update)
    buttons = buttons_mainmenu(context, chat_id)
    # TODO Tastatur mit Button für Hauptmenü belegen
    keyboardbutton = [[KeyboardButton('/start')]]
    context.bot.send_message(chat_id=chat_id,
                             text="Hallo! Willkommen beim ImmoScan-Bot.\nWie kann ich dir helfen?",
                             reply_markup=InlineKeyboardMarkup(buttons))


def main_menu(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    get_user(update)
    buttons = buttons_mainmenu(context, chat_id)
    try:
        update.callback_query.edit_message_text("Wie kann ich dir helfen?")
        update.callback_query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
    except:
        context.bot.send_message(chat_id=chat_id,
                                 text="Wie kann ich dir helfen?",
                                 reply_markup=InlineKeyboardMarkup(buttons))


def stop(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    context.bot.send_message(chat_id=chat_id, text='Suche abgebrochen!')
    main_menu(update, context)
    return ConversationHandler.END


def create_search(update: Update, context: CallbackContext) -> int:
    """Create a Job."""
    chat_id = update.effective_chat.id
    message_id = update.effective_message.message_id
    context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id)
    buttons = [['Immonet', 'ImmoScout24'], ['Andere (Fortgeschritten)'], ['⬅️ zurück']]
    context.bot.send_message(chat_id=chat_id,
                             text="Für welches Portal möchtest du eine Suche erstellen?",
                             reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True))
    return PORTAL


def portal(update: Update, context: CallbackContext) -> int:
    """Stores the info about the user and ends the conversation."""
    context.user_data['portal'] = update.message.text
    chat_id = update.effective_chat.id
    if context.user_data['portal'] in ['Immonet', 'ImmoScout24']:
        context.bot.send_message(chat_id=chat_id,
                                 text="Kopiere bitte die URL deiner Suche...",
                                 reply_markup=InlineKeyboardMarkup(
                                     [[InlineKeyboardButton("🤔 URL?!", callback_data='/help_url')]]))
        context.bot.send_message(chat_id=chat_id,
                                 text="...und füge sie hier ein.\n\n(Mit /stop, brichst du den Vorgang ab.)",
                                 reply_markup=ForceReply())
    else:
        context.bot.send_message(chat_id=chat_id,
                                 text=clean_message(
                                     "Bei diesem Vorgehen wird der aktuelle Stand der angegebenen Webseite gespeichert. In jedem Durchgang wird auf eine Änderung überprüft und, falls dies zutrifft, du informiert. Dadurch können auch nicht unterstützte Seiten beobachtet werden.\n\n"
                                     "Hinweis: Falls die Webseite dynamisch gestaltet ist und sich automatisch ändert, wirst du sehr viele falsche Benachrichtigungen erhalten.\n"
                                     "Auch kann über eine interaktive Webseite ggf. keine Änderung aufgrund der Verwendung von JavaScript festgestellt werden.\n\n"
                                     "(Mit /stop, brichst du den Vorgang ab.)"),
                                 reply_markup=ForceReply(),
                                 parse_mode='MarkdownV2')
    if context.user_data['portal'] == 'Immonet':
        return URL_IMMONET
    elif context.user_data['portal'] == 'ImmoScout24':
        return URL_IMMOSCOUT24
    else:
        return URL_OTHER


def portal_wrong(update: Update, context: CallbackContext) -> int:
    """Stores the info about the user and ends the conversation."""
    chat_id = update.effective_chat.id
    if context.user_data['portal'] not in ['Immonet', 'ImmoScout24']:
        context.bot.send_message(chat_id=chat_id,
                                 text=clean_message(
                                     f"Ungültige URL. Bitte versuche es erneut.\n\n"
                                     f"Wie lautet die URL deiner Suche?\n\n"
                                     f"(Mit /stop, brichst du den Vorgang ab.)"),
                                 parse_mode='MarkdownV2',
                                 reply_markup=ForceReply(),
                                 disable_web_page_preview=True)
    else:
        if context.user_data['portal'] == 'Immonet':
            textbaustein = "https://www.immonet.de/immobiliensuche/"
        elif context.user_data['portal'] == 'ImmoScout24':
            textbaustein = "https://www.immobilienscout24.de/Suche/"
        context.bot.send_message(chat_id=chat_id,
                                 text=clean_message(
                                     f"Ungültige URL. Bitte versuche es erneut.\n\n"
                                     f"Achte darauf, dass dieser 1:1 aus der Suche von {context.user_data['portal']} entnommen wurde und wie folgt beginnt:\n"
                                     f"_{textbaustein}..._\n\n"
                                     f"Wie lautet die URL deiner Suche?\n\n"
                                     f"(Mit /stop, brichst du den Vorgang ab.)"),
                                 parse_mode='MarkdownV2',
                                 reply_markup=ForceReply(),
                                 disable_web_page_preview=True)
    if context.user_data['portal'] == 'Immonet':
        return URL_IMMONET
    elif context.user_data['portal'] == 'ImmoScout24':
        return URL_IMMOSCOUT24
    else:
        return URL_OTHER


def url(update: Update, context: CallbackContext) -> int:
    """Stores the info about the user and ends the conversation."""
    context.user_data['url'] = update.message.text
    chat_id = update.effective_chat.id
    context.bot.send_message(chat_id=chat_id,
                             text="Um deine Suche wiederzufinden, benötigst du eine Beschreibung dafür.\n"
                                  "Wie möchtest du diese Suche nennen?\n\n"
                                  "(Mit /stop, brichst du den Vorgang ab.)",
                             reply_markup=ForceReply())
    return DESCRIPTION


def description(update: Update, context: CallbackContext) -> int:
    """Stores the info about the user and ends the conversation."""
    user_data = context.user_data
    portal = user_data['portal']
    if portal == 'Immonet':
        portal_id = 1
    elif portal == 'ImmoScout24':
        portal_id = 2
    else:
        portal_id = 0
    url = user_data['url']
    description = update.message.text
    chat_id = update.effective_chat.id
    db.execute(
        f"INSERT INTO jobs (chat_id, beschreibung, url, type) VALUES ('{chat_id}','{description}', '{url}', {portal_id})")
    db.commit()
    if not search_isrunning(context, chat_id):
        addition = '\n\nBisher läuft keine Suche.\nStarte deine Suche mit einem Klick auf _zurück_ und _Suche starten_.'
    else:
        addition = ''
    context.bot.send_message(chat_id=chat_id,
                             text=clean_message(
                                 f'Deine Suche auf *{portal}* mit der Beschreibung *{description}* wurde gespeichert'
                                 f'{addition}'),
                             parse_mode='MarkdownV2',
                             reply_markup=InlineKeyboardMarkup(back),
                             )
    return ConversationHandler.END


def start_search(update: Update, context: CallbackContext) -> None:
    """Start search."""
    update.callback_query.edit_message_reply_markup(None)
    chat_id = update.effective_chat.id
    start_search_func(update, context, chat_id)


def stop_search(update: Update, context: CallbackContext) -> None:
    """Stop search."""
    update.callback_query.edit_message_reply_markup(None)
    chat_id = update.effective_chat.id
    buttons = [[InlineKeyboardButton("⬅️ zurück", callback_data='/main_menu')]]
    if not job_exists(chat_id):
        text = 'Es läuft aktuell keine Suche.'
    else:
        current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
        for job in current_jobs:
            job.schedule_removal()
        text = 'Deine Suche wurde beendet.'
    context.bot.send_message(chat_id=chat_id,
                             text=text,
                             reply_markup=InlineKeyboardMarkup(back))


def edit_search(update: Update, context: CallbackContext) -> None:
    update.callback_query.edit_message_reply_markup(None)
    chat_id = update.effective_chat.id
    searches = db.execute(f"SELECT * FROM jobs WHERE chat_id = {int(chat_id)}").fetchall()
    if not searches:
        update.callback_query.edit_message_text(
            text='Du hast keine gespeicherten Suchen.',
            reply_markup=InlineKeyboardMarkup(back))
    else:
        buttons = []
        for search in searches:
            buttons.append([InlineKeyboardButton(search[1], callback_data='/search ' + str(search[4]))])
        buttons.append([InlineKeyboardButton("❕ Suchergebnisse zurücksetzen",
                                             callback_data='/suche_resetten')])
        buttons.append([InlineKeyboardButton("⬅️ zurück", callback_data='/main_menu')])
        update.callback_query.edit_message_text(
            text='Du hast folgende gespeicherten Suchen...',
            reply_markup=InlineKeyboardMarkup(buttons))


def reset_search(update: Update, context: CallbackContext) -> None:
    """Reset Search Data from Database"""
    chat_id = update.effective_chat.id
    exists = db.execute(f"SELECT * FROM wohnungen WHERE chat_id = {chat_id}").fetchone()
    if exists:
        db.execute(f"DELETE FROM wohnungen WHERE chat_id = {chat_id};")
        db.execute(f"UPDATE jobs SET hash = NULL WHERE chat_id = {chat_id};")
        db.commit()
        text = 'Die Suchdaten wurden zurückgesetzt.'
    else:
        text = 'Es sind keine gespeicherten Suchdaten vorhanden.'
    update.callback_query.edit_message_text(text)
    update.callback_query.edit_message_reply_markup(InlineKeyboardMarkup(back))


def pay(update: Update, context: CallbackContext) -> None:
    """Sends an invoice without shipping-payment."""
    chat_id = update.effective_chat.id
    title = "ImmoScan Bot"
    description = "Hilft die ImmoScan bei der Suche nach deinem Traumobjekt?\n\n" \
                  "Mit einem kleinen Beitrag in Höhe eines Kaffees kannst du mich bei der Entwicklung und dem Betrieb der Infrastruktur unterstützen."
    # select a payload just for you to recognize its the donation from your bot
    payload = "ImmoScan"
    # In order to get a provider_token see https://core.telegram.org/bots/payments#getting-a-token
    provider_token = '350862534:LIVE:MWM5ZGEzY2Q3NjQ2'
    currency = "EUR"
    price = 300
    prices = [LabeledPrice("ImmoScan Spende", price)]
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton(f"Unterstützen", pay=True)],
                                    [InlineKeyboardButton("⬅️ zurück", callback_data='/main_menu')]])
    context.bot.send_invoice(
        chat_id, title, description, payload, provider_token, currency, prices, suggested_tip_amounts=[200, 500, 700],
        max_tip_amount=10000, reply_markup=buttons
    )


def precheckout_callback(update: Update, context: CallbackContext) -> None:
    """Answers the PreQecheckoutQuery"""
    query = update.pre_checkout_query
    # check the payload, is this from your bot?
    if query.invoice_payload != 'ImmoScan':
        # answer False pre_checkout_query
        query.answer(ok=False, error_message="Leider ist etwas schief gelaufen...")
    else:
        query.answer(ok=True)


def successful_payment_callback(update: Update, context: CallbackContext) -> None:
    """Confirms the successful payment."""
    chat_id = update.effective_chat.id
    user = get_user(update)
    db.execute(f"UPDATE users SET paid = 1, paid_date = DATE() WHERE user_id = {user[1]};")
    db.commit()
    context.bot.send_message(chat_id=chat_id,
                             text="Vielen Dank für deine Unterstützung!",
                             reply_markup=InlineKeyboardMarkup(back))


def help(update: Update, context: CallbackContext) -> None:
    update.callback_query.edit_message_text(
        clean_message(
            "Ich helfe dir gerne bei Fragen zu ImmoScan!\n\n"
            "*Was ist das für ein Programm?*\n"
            "ImmoScan hilft dir dabei die Portale von [_Immonet_](https://www.immonet.de) und [_Immobilienscout_](https://www.immobilienscout24.de) nach den neuesten Angeboten zu durchsuchen.\n"
            "Dabei kannst du so viele verschiedene Suchen einspeichern wie du möchtest. ImmoScan informiert dich, sobald es neue Anzeigen gibt.\n\n"
            "*Warum kann ich das nicht über die offiziellen Newsletter machen?*\n"
            "Das kannst du. Da diese jedoch immer frühestens nach einer Stunde über ein neues Angebot informieren, haben schnellere Interessenten einen Vorteil. Insbesondere in Suchgebieten mit hoher Nachfrage (wie beispielsweise Hamburg, Berlin und München) zählt jede Sekunde. ImmoScan sucht jede Minute nach neuen Angeboten und hilft dir dadurch frühstmöglich die gewünschten Informationen zu erhalten.\n\n"
            "*Wie bediene ich ImmoScan?*\n"
            "Es wurde versucht die Bedienung so einfach wie möglich zu gestalten.\n\n"
            "Mit dem Befehl '/start' gelangst du in das Hauptmenü. Von dort kannst du _Eine neue Suche erstellen_, bereits bestehende _Suchen bearbeiten_ sowie deinen Suchrobotor _Starten_ oder _Beenden_.\n\n"
            "*Wie erstelle ich eine neue Suche?*\n"
            "Klicke dafür auf _Neue Suche erstellen_. Wähle dort dein gewünschte Portal aus.\n"
            "Im Anschluss wirst du gebeten die Such-URL einzugeben.\n\n"
            "Gehe dafür auf die zu durchsuchende Seite (Immonet oder ImmoScout24), gebe deine gewünschten Suchparameter ein und klicke auf Suchen. Diese URL gibst du dann an ImmoScan zurück. Auch ein Klick auf _Teilen_ an ImmoScan übergibt diesen Link.\n\n"
            "Zuletzt wird eine Beschreibung der Suche benötigt. Wenn du mehrere Suchen anlegst, kannst du diese leichter unterscheiden.\n\n"
            "*Warum gibt es keine Suche für Immowelt?*\n"
            "Immonet und Immowelt gehören zusammen und zeigen dieselben Suchergebnisse an. Daher gibt es nur die Suche für Immonet.\n\n"
            "*Wie suche ich auf anderen Seiten wie beispielsweise direkt bei Wohnungsbaugenossenschaften?*\n"
            "Unter _Neue Suche erstellen_ gibt es die Möglichkeit auch _Andere_ Seiten zu durchsuchen. Dabei wird der aktuelle Stand der Webseite gespeichert und stetig auf Veränderungen geprüft. Dies funktioniert nur bei Seiten, welche ihre Suche direkt in der URL speichern."
        ),
        parse_mode='MarkdownV2',
        disable_web_page_preview=True
    )
    update.callback_query.edit_message_reply_markup((InlineKeyboardMarkup(back)))


def help_url(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    context.bot.send_photo(
        chat_id=chat_id,
        photo='https://i.ibb.co/y5yHN35/Anleitung-URL.jpg',
        caption='1️⃣ Besuche ImmoScout24 bzw. Immonet und gebe dort die Parameter für deine gewünschte Suche ein.\n\n'
                '2️⃣Klicke auf Suchen, bis die Suchergebnisse angezeigt werden.\n\n'
                '3️⃣Wenn du in der Ansicht bist in der alle deine Suchergebnisse angezeigt werden, bist du richtig. Kopiere den Link und füge ihn bei ImmoScan ein.',
        reply_markup=(
            InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verstanden", callback_data='/verstanden')]]))
    )


def search_results(context: CallbackContext) -> None:
    """Send the alarm message."""
    job = context.job
    searches = db.execute(f"SELECT * FROM jobs WHERE chat_id = {int(job.name)}").fetchall()
    for search in searches:
        if search[3] == 1:
            immonet_search(context, search[4])
        elif search[3] == 2:
            immoscout24_search(context, search[4])
        elif search[3] == 0:
            other_search(context, search[4])


def verstanden(update: Update, context: CallbackContext):
    update.callback_query.message.delete()


def delete(pk):
    db.execute(f"DELETE FROM jobs WHERE pk = {pk}")
    db.commit()


def functions(update: Update, context: CallbackContext):
    input = update.callback_query.data
    chat_id = update.effective_chat.id
    command = input.split(' ')
    pk = command[1]
    description = db.execute(f"SELECT beschreibung FROM jobs WHERE pk = {pk}").fetchone()
    if command[0] == '/search':
        update.callback_query.edit_message_text(description[0])
        buttons = [
            [InlineKeyboardButton("🗑 Löschen", callback_data='/delete ' + str(pk))],
            [InlineKeyboardButton("✒️ Umbenennen", callback_data='/rename ' + str(pk))],
            [InlineKeyboardButton("⬅️ zurück", callback_data='/suche_bearbeiten')]
        ]
        update.callback_query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
        # update.callback_query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
    elif command[0] == '/delete':
        delete(command[1])
        context.bot.send_message(chat_id=chat_id,
                                 text=f"{description[0]} gelöscht.")
        edit_search(update, context)


def unknown(update: Update, context: CallbackContext):
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text='Schreibe "/start" um mit mir zu interagieren.')


def main():
    # Initialize Bot
    updater = Updater(token=token, use_context=True, request_kwargs={'read_timeout': 15, 'connect_timeout': 40})
    dispatcher = updater.dispatcher
    global db
    db = init_db()

    ### Message for Update
    jobs = db.execute(f"SELECT DISTINCT chat_id FROM jobs").fetchall()
    for job in jobs:
        updater.bot.send_message(chat_id=job[0],
                                 text=clean_message(
                                     f'*ImmoScan wurde geupdated*\n'
                                     f'Folgende Neuerungen wurden übernommen:\n\n'
                                     f'{update}'
                                     f'Um weiterhin Ergebnisse zu erhalten, klicke auf "/start" und starte deine Suche erneut.'),
                                 parse_mode='MarkdownV2')

    ###Register Handlers
    dispatcher.add_handler(CommandHandler('start', start))

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(create_search, pattern='/suche_erstellen')],
        states={
            PORTAL: [MessageHandler(Filters.regex('^(Immonet|ImmoScout24|Andere \\(Fortgeschritten\\))$'), portal),
                     MessageHandler(Filters.regex('⬅️ zurück'), stop)],
            URL_IMMONET: [MessageHandler(
                Filters.regex(r'http(s)?://(www.)immonet.de/immobiliensuche/*'), url),
                MessageHandler(Filters.text & (~ Filters.command), portal_wrong)],
            URL_IMMOSCOUT24: [MessageHandler(Filters.regex(
                r'http(s)?://(www.)immobilienscout24.de/Suche/*'), url),
                MessageHandler(Filters.text & (~ Filters.command), portal_wrong)],
            URL_OTHER: [MessageHandler(Filters.entity('url'), url),
                        MessageHandler(Filters.text & (~ Filters.command), portal_wrong)],
            DESCRIPTION: [MessageHandler(Filters.text & (~ Filters.command), description)],
        },
        fallbacks=[CommandHandler('stop', stop)],
    )

    dispatcher.add_handler(conv_handler)
    updater.dispatcher.add_handler(CallbackQueryHandler(main_menu, pattern='/main_menu'))
    updater.dispatcher.add_handler(CallbackQueryHandler(help_url, pattern='/help_url'))
    updater.dispatcher.add_handler(CallbackQueryHandler(help, pattern='/help'))
    updater.dispatcher.add_handler(CallbackQueryHandler(pay, pattern='/pay'))
    dispatcher.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    dispatcher.add_handler(MessageHandler(Filters.successful_payment, successful_payment_callback))
    updater.dispatcher.add_handler(CallbackQueryHandler(start_search, pattern='/suche_starten'))
    updater.dispatcher.add_handler(CallbackQueryHandler(stop_search, pattern='/suche_stoppen'))
    updater.dispatcher.add_handler(CallbackQueryHandler(reset_search, pattern='/suche_resetten'))
    updater.dispatcher.add_handler(CallbackQueryHandler(edit_search, pattern='/suche_bearbeiten'))
    updater.dispatcher.add_handler(CallbackQueryHandler(verstanden, pattern='/verstanden'))
    dispatcher.add_handler(CallbackQueryHandler(functions))

    # Error Handler
    dispatcher.add_handler(MessageHandler(Filters.command, unknown))

    # Start Bot
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
