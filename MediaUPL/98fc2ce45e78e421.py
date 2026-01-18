# meta developer: @dzhct
# scope: anime_ai
# requires: requests

import logging
import json
import random
import requests
from telethon import Button
from telethon.tl.types import Message

from .. import loader, utils

logger = logging.getLogger(__name__)

@loader.tds
class AnimeAIMod(loader.Module):
    """
    Умный рекомендатор аниме на базе Llama 3.3 70B (OpenRouter).
    Автоматически перебирает бесплатные модели при ошибках.
    """

    strings = {
        "name": "AnimeAI",
        "cfg_api_key": "API Key от OpenRouter",
        "no_key": (
            "<b>⚠️ Не указан OpenRouter API Key!</b>\n"
            "1. <a href='https://openrouter.ai/settings/keys'>Получи ключ здесь (бесплатно)</a>\n"
            "2. Введи <code>.config AnimeAI</code> и вставь его."
        ),
        "thinking": "<b>🧠 Llama 3.3 подбирает тайтл...</b>",
        "api_err": "<b>⚠️ Все модели перегружены!</b>\nПопробуй через минуту.",
        "json_err": "<b>⚠️ Ошибка AI:</b> Модель вернула мусор. Жми кнопку еще раз.",
        "caption": "🎬 <b>{title}</b>\n\n🎭 <b>Жанры:</b> {genres}\n⭐ <b>Рейтинг:</b> {score}\n\n📝 <i>{synopsis}</i>",
        "btn_watch": "✅ Смотрел",
        "btn_skip": "⏩ Скип",
        "btn_close": "❌ Закрыть",
        "added": "✅ <b>Добавлено в список!</b>",
        "list_header": "📺 <b>Твой список просмотренного ({count}):</b>\n\n",
        "list_empty": "📭 Ты еще ничего не сохранил.",
        "page": "\n<i>Страница {}/{}</i>"
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "api_key",
                None,
                self.strings["cfg_api_key"],
                validator=loader.validators.Hidden(),
            ),
        )
        self.current_recommendation = None

    async def client_ready(self, client, db):
        self.client = client
        self.db = db

    async def _query_openrouter(self, watched_list, user_query=None):
        """Запрос к OpenRouter с перебором моделей"""
        url = "https://openrouter.ai/api/v1/chat/completions"
        api_key = self.config["api_key"]
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/hikariatama/Heroku", 
            "X-Title": "Heroku Userbot",
        }

        # Контекст (последние 50)
        watched_str = ", ".join(watched_list[-50:]) if watched_list else "None"
        
        system_prompt = (
            "You are an anime recommendation expert. "
            "Output ONLY valid JSON. Do NOT write markdown code blocks. Do NOT write explanations. "
            "JSON keys: title (Romaji), title_ru (Russian), genres, synopsis (Russian, max 2 sentences)."
        )
        
        user_prompt = (
            f"User watched: [{watched_str}]. "
            f"Request: '{user_query if user_query else 'Recommend something high rated and popular'}'. "
            "Recommend 1 anime NOT in watched list."
        )

        # Список моделей. Первая - самая крутая. Остальные - запасные.
        models_to_try = [
            "meta-llama/llama-3.3-70b-instruct:free",
            "meta-llama/llama-3.1-8b-instruct:free",
            "google/gemini-2.0-flash-lite-preview-02-05:free",
            "google/gemini-2.0-flash-exp:free",
            "mistralai/mistral-7b-instruct:free"
        ]

        for model in models_to_try:
            data = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.8,
                "top_p": 0.9,
            }

            try:
                response = await utils.run_sync(requests.post, url, headers=headers, json=data)
                
                if response.status_code != 200:
                    logger.error(f"OpenRouter Error ({model}): {response.text}")
                    continue 
                
                response_json = response.json()
                
                if 'choices' not in response_json or not response_json['choices']:
                    continue

                content = response_json['choices'][0]['message']['content']
                # Агрессивная чистка от маркдауна, который любят Llama
                content = content.replace("```json", "").replace("```", "").strip()
                
                return json.loads(content)

            except Exception as e:
                logger.error(f"AI Parse Error ({model}): {e}")
                continue

        return None 

    async def _get_poster_jikan(self, title):
        """Поиск постера через Jikan API"""
        try:
            url = f"https://api.jikan.moe/v4/anime?q={title}&limit=1"
            r = await utils.run_sync(requests.get, url)
            if r.status_code == 200:
                data = r.json()
                if data.get('data'):
                    anime = data['data'][0]
                    return {
                        'image': anime['images']['jpg']['large_image_url'],
                        'score': anime.get('score', 'N/A'),
                        'url': anime.get('url', '')
                    }
        except Exception as e:
            logger.error(f"Jikan Error: {e}")
        return None

    @loader.command(ru_doc="[запрос] - Рекомендация от AI")
    async def anime(self, message: Message):
        """Get AI anime recommendation"""
        if not self.config["api_key"]:
            await utils.answer(message, self.strings["no_key"])
            return

        args = utils.get_args_raw(message)
        msg = await utils.answer(message, self.strings["thinking"])
        
        await self._generate_card(msg, args, is_new=True)

    async def _generate_card(self, message, query=None, is_new=False):
        """Генерация карточки"""
        watched = self.db.get("AnimeAI", "watched", [])
        
        ai_data = await self._query_openrouter(watched, query)
        
        if not ai_data:
            err = self.strings["api_err"]
            if is_new: await utils.answer(message, err)
            else: await message.edit(err)
            return

        jikan_data = await self._get_poster_jikan(ai_data['title'])
        
        image_url = jikan_data['image'] if jikan_data else None
        score = jikan_data['score'] if jikan_data else "N/A"

        caption = self.strings["caption"].format(
            title=f"{ai_data['title']} / {ai_data['title_ru']}",
            genres=ai_data['genres'],
            score=score,
            synopsis=ai_data['synopsis']
        )

        self.current_recommendation = ai_data['title']
        short_title = ai_data['title'][:40] 

        kb = [
            [
                Button.inline(self.strings["btn_watch"], data=f"ai_w:{short_title}"),
                Button.inline(self.strings["btn_skip"], data="ai_s")
            ],
            [
                Button.inline(self.strings["btn_close"], data="ai_c")
            ]
        ]

        chat_id = message.chat_id
        try:
            await message.delete()
        except: pass
        
        if image_url:
            await self.client.send_file(chat_id, image_url, caption=caption, buttons=kb)
        else:
            await self.client.send_message(chat_id, caption, buttons=kb)

    @loader.callback_handler
    async def _cb_handler(self, call):
        try:
            data = call.data.decode()
        except: return

        if not data.startswith("ai_"): return

        action = data.split(":")[0]

        if action == "ai_c":
            await call.delete()
            return

        if action == "ai_s":
            await utils.answer(call, self.strings["thinking"])
            await self._generate_card(call.message)
            return

        if action == "ai_w":
            title = getattr(self, "current_recommendation", None)
            if not title and len(data.split(":")) > 1:
                title = data.split(":", 1)[1]
            
            if title:
                watched = self.db.get("AnimeAI", "watched", [])
                if title not in watched:
                    watched.append(title)
                    self.db.set("AnimeAI", "watched", watched)
            
            await call.answer(self.strings["added"], show_alert=True)
            await utils.answer(call, self.strings["thinking"])
            await self._generate_card(call.message)
            return

        if action.startswith("ai_p"):
            try:
                page = int(data.split(":")[1])
                await self._render_list(call, page)
            except: pass

    @loader.command(ru_doc="Список просмотренного")
    async def animelist(self, message: Message):
        """Show watched list"""
        await self._render_list(message, 1)

    async def _render_list(self, entity, page):
        watched = self.db.get("AnimeAI", "watched", [])
        
        if not watched:
            if isinstance(entity, Message):
                await utils.answer(entity, self.strings["list_empty"])
            else:
                await entity.answer(self.strings["list_empty"], show_alert=True)
            return

        per_page = 15
        total_pages = (len(watched) - 1) // per_page + 1
        if page < 1: page = 1
        if page > total_pages: page = total_pages

        start = (page - 1) * per_page
        end = start + per_page
        chunk = watched[start:end]

        text = self.strings["list_header"].format(count=len(watched))
        for i, title in enumerate(chunk, start=start + 1):
            text += f"{i}. <blockquote>{title}</blockquote>\n"
        
        text += self.strings["page"].format(page, total_pages)

        kb = []
        row = []
        if page > 1:
            row.append(Button.inline("⬅️", data=f"ai_p:{page-1}"))
        row.append(Button.inline("❌", data="ai_c"))
        if page < total_pages:
            row.append(Button.inline("➡️", data=f"ai_p:{page+1}"))
        kb.append(row)

        if isinstance(entity, Message):
            await utils.answer(entity, text, reply_markup=kb)
        else:
            await entity.edit(text, buttons=kb)