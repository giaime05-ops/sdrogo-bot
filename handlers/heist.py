import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from state import HEIST_GAMES, USER_INVENTORIES
from storage import add_user_coins

async def handle_heist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    stage = parts[1]
    owner_id = int(parts[2])

    if query.from_user.id != owner_id:
        return

    user_id = query.from_user.id
    if user_id not in HEIST_GAMES:
        await query.edit_message_text("❌ Sessione Rapina terminata.")
        return

    game = HEIST_GAMES[user_id]
    chat_id = game["chat_id"]

    if stage == "lvl1":
        keyboard = [
            [InlineKeyboardButton("🔴 Cavo Rosso", callback_data=f"heist_lvl1res_fail_{user_id}")],
            [InlineKeyboardButton("🔵 Cavo Blu", callback_data=f"heist_lvl1res_win_{user_id}")],
            [InlineKeyboardButton("🟡 Cavo Giallo", callback_data=f"heist_lvl1res_fail_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text("🔓 <b>LIVELLO 1: DISATTIVAZIONE ALLARME</b>\n\nQuale cavo tagli per disattivare l'allarme?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl1res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>ALLARME SCATTATO!</b> Le guardie ti hanno preso. Fuga fallita!")
        else:
            game["level"] = 2
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 50 $SDG ed esci)", callback_data=f"heist_cashout_50_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 2 (Guardia)", callback_data=f"heist_lvl2_{user_id}")]
            ]
            await query.edit_message_text("✅ <b>LIVELLO 1 SUPERATO!</b>\nPremio accumulato: <code>💳 50 $SDG</code>.\n\nCosa vuoi fare?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl2":
        p_hand = random.randint(15, 21)
        g_hand = random.randint(14, 21)
        
        if p_hand >= g_hand:
            game["level"] = 3
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 100 $SDG ed esci)", callback_data=f"heist_cashout_100_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 3 (Laser)", callback_data=f"heist_lvl3_{user_id}")]
            ]
            await query.edit_message_text(f"👮 <b>LIVELLO 2 SUPERATO!</b>\nHai messo KO la guardia ({p_hand} vs {g_hand})!\nPremio accumulato: <code>💳 100 $SDG</code>.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
            del HEIST_GAMES[user_id]
            await query.edit_message_text(f"👮 <b>LA GUARDIA TI HA VISTO!</b> ({g_hand} vs {p_hand})\nSei stato arrestato! Fuga fallita.")

    elif stage == "lvl3":
        keyboard = [
            [InlineKeyboardButton("🚪 Porta A", callback_data=f"heist_lvl3res_fail_{user_id}")],
            [InlineKeyboardButton("🚪 Porta B", callback_data=f"heist_lvl3res_win_{user_id}")],
            [InlineKeyboardButton("🚪 Porta C", callback_data=f"heist_lvl3res_fail_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text("⚡ <b>LIVELLO 3: CAMPO LASER</b>\n\nTre porte davanti a te. Solo una non ha i laser attivi!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl3res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("⚡ <b>COLPITO DAL LASER!</b> L'allarme è scattato. Fuga fallita!")
        else:
            game["level"] = 4
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 180 $SDG ed esci)", callback_data=f"heist_cashout_180_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 4 (Cassaforte)", callback_data=f"heist_lvl4_{user_id}")]
            ]
            await query.edit_message_text("⚡ <b>LIVELLO 3 SUPERATO!</b>\nPremio accumulato: <code>💳 180 $SDG</code>.\n\nCosa vuoi fare?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl4":
        keyboard = [
            [InlineKeyboardButton("🔐 Codice 4-8-1 (Sbagliato)", callback_data=f"heist_lvl4res_fail_{user_id}")],
            [InlineKeyboardButton("🔐 Codice 7-7-7 (Sbagliato)", callback_data=f"heist_lvl4res_fail_{user_id}")],
            [InlineKeyboardButton("🔐 Codice 1-2-3 (Corretto)", callback_data=f"heist_lvl4res_win_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text("🔐 <b>LIVELLO 4: LA CASSAFORTE</b>\n\nTrova la combinazione corretta prima che scada il tempo!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl4res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>COMBINAZIONE ERRATA!</b> La cassaforte si è bloccata. Fuga fallita!")
        else:
            game["level"] = 5
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 300 $SDG ed esci)", callback_data=f"heist_cashout_300_{user_id}")],
                [InlineKeyboardButton("🔥 SFIDA IL LIVELLO 5 FINALE!", callback_data=f"heist_lvl5_{user_id}")]
            ]
            await query.edit_message_text("🔐 <b>LIVELLO 4 SUPERATO!</b>\nPremio accumulato: <code>💳 300 $SDG</code>.\n\nSei ad un passo dalla gloria!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl5":
        keyboard = [
            [InlineKeyboardButton("🚁 Elicottero sul Tetto", callback_data=f"heist_lvl5res_win_{user_id}")],
            [InlineKeyboardButton("🚗 Fuga in Tunnel", callback_data=f"heist_lvl5res_fail_{user_id}")]
        ]
        await query.edit_message_text("🚁 <b>LIVELLO 5: LA FUGA FINALE</b>\n\nCome scappi col bottino?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl5res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("🚔 <b>LA POLIZIA TI HA CIRCONDATO IN TUNNEL!</b> Fuga fallita all'ultimo secondo!")
        else:
            del HEIST_GAMES[user_id]
            add_user_coins(chat_id, user_id, 600)
            
            inv_key = f"{chat_id}_{user_id}"
            if inv_key not in USER_INVENTORIES: USER_INVENTORIES[inv_key] = {"titles": 0, "persecutes": 0, "stars": 0}
            USER_INVENTORIES[inv_key]["stars"] = USER_INVENTORIES[inv_key].get("stars", 0) + 1
            USER_INVENTORIES[inv_key]["titles"] += 1
            USER_INVENTORIES[inv_key]["persecutes"] += 1
            
            await query.edit_message_text("🏆 <b>RAPINA PERFETTA COMPLETATA!</b>\nHai vinto +600 $SDG, 1 Titolo e 1 Persecuzione Gratis + 1 STELLA ⭐!")
            
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"👑 <b>COLPO DEL SECOLO!</b> 🏢\n\n<b>{query.from_user.first_name}</b> ha svaligiato il Caveau di Sdrogo Heist arrivando al 5° Livello!\nGuadagna <b>💳 600 $SDG</b> e 1 STELLA ⭐ di prestigio in classifica!",
                    parse_mode="HTML"
                )
            except Exception: pass

    elif stage == "cashout":
        amount = int(parts[2])
        del HEIST_GAMES[user_id]
        add_user_coins(chat_id, user_id, amount)
        await query.edit_message_text(f"💰 <b>CASHOUT EFFETTUATO!</b> Ti ritiri dalla rapina incassando <b>+💳 {amount} $SDG</b>!")

def register(app):
    app.add_handler(CallbackQueryHandler(handle_heist_callback, pattern="^heist_"))
