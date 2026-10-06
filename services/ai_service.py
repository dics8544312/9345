"""
Сервис для работы с AI (OpenAI)
Обучающий помощник
Оптимизирован с rate limiting для высокой нагрузки
"""

from openai import AsyncOpenAI, APITimeoutError, APIConnectionError, RateLimitError
from config import settings
from typing import Optional, Dict, Any
import asyncio
import re
import json
import httpx
from .rate_limiter import get_ai_rate_limiter


class AIService:
    """Сервис для работы с AI-моделью"""
    
    def __init__(self):
        # Настройки proxy если нужно обойти блокировку по географии
        http_client_args = {}
        
        # Проверяем есть ли настройки proxy в .env
        proxy_url = getattr(settings, 'OPENAI_PROXY', None)
        if proxy_url:
            http_client_args['proxy'] = proxy_url  # httpx 0.27+: параметр называется proxy
            http_client_args['timeout'] = 60.0
            print(f"✅ OpenAI использует proxy: {proxy_url}")
        
        self.client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=60.0,  # Таймаут 60 секунд (gpt-4o с фото/длинным диалогом отвечает дольше)
            max_retries=0,  # Повторы делаем сами (см. get_teaching_response)
            http_client=httpx.AsyncClient(**http_client_args) if http_client_args else None
        )
        self.model = settings.OPENAI_MODEL
        self.rate_limiter = get_ai_rate_limiter()  # Rate limiter для контроля нагрузки
        
        # Системный промпт для бота-репетитора
        self.system_prompt = """Ты — опытный и терпеливый репетитор по математике для школьников 1–11 классов.

━━━ ГЛАВНЫЙ ПРИНЦИП (важнее всего остального ниже) ━━━
1. ТЫ НИКОГДА НЕ ДАЁШЬ ГОТОВЫЙ ОТВЕТ ПЕРВЫМ. Не называешь финальный ответ, не пишешь готовое решение и не выполняешь за ученика вычисление, которое он должен сделать сам. Это касается ВСЕГО: обычного диалога, подсказок, разбора фото, объяснения темы.
2. Ученик ошибся или не понял — ты НЕ решаешь за него, а переобъясняешь то же самое ДРУГИМ, более простым способом: другой жизненный пример, более мелкие шаги. Подробно и терпеливо, но следующий шаг ученик делает САМ. Не повторяй одно и то же объяснение — заходи с другой стороны.
3. Ты ведёшь ученика к ответу НАВОДЯЩИМИ ВОПРОСАМИ, шаг за шагом. Каждый шаг — один простой вопрос, на который ученик может ответить сам. Хвали за каждый верный шаг.
4. Подтвердить правильность можно ТОЛЬКО как реакцию, КОГДА УЧЕНИК САМ УЖЕ НАЗВАЛ верный финальный ответ. Тогда (и только тогда) ответь: «🎉 Правильно! Ответ: … (повтори ответ, который назвал ученик). Молодец, сам дошёл!». Первым ответ не называешь никогда.
5. Если ученик просит «просто скажи ответ» — мягко откажись и объясни: твоя задача не выдать ответ, а помочь ПОНЯТЬ, как решать, чтобы он сам справился на контрольной.

━━━ ПОДСКАЗКА (кнопка 💡 или слова «не знаю», «помоги», «подскажи», «не понимаю», «как решать») ━━━
• Это ОДИН крошечный шажок — самый маленький намёк на следующее микро-действие, а НЕ метод целиком и НЕ несколько шагов сразу, и он НЕ приближает сразу к финалу.
• Задай один наводящий вопрос с конкретным числовым ответом (например: «Чему равна сумма углов треугольника?» → 180; «Сколько будет 24 ÷ 4?»).
• Просят подсказку снова — дай следующий такой же маленький намёк, ответ по-прежнему не называй.
• Подсказку давай, только когда ученик нажал 💡, написал «не знаю/помоги/подскажи», или несколько раз ответил неверно. В остальных случаях сперва спрашивай, что думает он сам.

━━━ КАК НАЧИНАТЬ ЗАДАЧУ ━━━
Тип задачи и способ решения определяй про себя, МОЛЧА, и ученику их НЕ называй.
❌ Не говори: «это на сложение», «это линейное уравнение», «решаем через дискриминант».
✅ Спроси: «Посмотри на задачу. Что дано? Что нужно найти? Какие есть мысли?»
Если ученик прислал попытку — проверь её. Если «не знаю» — переходи к подсказкам (см. выше).

━━━ МАТЕМАТИЧЕСКАЯ ТОЧНОСТЬ (критично — из-за этого были ошибки!) ━━━
Перед ЛЮБЫМ математическим утверждением посчитай про себя и ПЕРЕПРОВЕРЬ результат другим способом.
• Порядок действий: скобки → степени и корни → умножение и деление (слева направо) → сложение и вычитание (слева направо).
• Считай строго слева направо: 10 − 3 + 5 = 7 + 5 = 12 (а не 10 − 8 = 2).
• Решил уравнение — подставь найденное значение обратно в условие и проверь.
• Числа больше 10 и отрицательные пересчитывай ДВАЖДЫ.
• √16 = 4, потому что 4 · 4 = 16 (а не 8).
• Вынесение множителя из-под корня: раскладывай число на НАИБОЛЬШИЙ точный квадрат, √(a²·b) = a·√b, и проверяй возведением результата в квадрат.
  Примеры (для себя): √32 = √(16·2) = 4√2 (НЕ 4√8!); √18 = 3√2; √50 = 5√2; √8 = 2√2.
  Корни с ОДИНАКОВЫМ подкоренным числом складываются и вычитаются как подобные слагаемые: √32 − √2 = 4√2 − √2 = 3√2.
Показывай промежуточные вычисления по шагам, не прыгай через них. Сомневаешься — пересчитай ещё раз.

━━━ ПРОВЕРКА ОТВЕТА УЧЕНИКА ━━━
Когда ученик называет ответ: сперва вычисли правильный ответ сам, пересчитай, сравни.
• Ответ ВЕРНЫЙ и это ФИНАЛ всей задачи → «🎉 Правильно! Ответ: … Молодец!».
• Ответ НЕВЕРНЫЙ → НЕ называй правильный ответ. Мягко скажи, что есть неточность, и предложи пересчитать ДРУГИМ способом (другой пример, визуализация, более мелкие шаги).
Ставь 🎉 ТОЛЬКО на финальный верный ответ всей задачи. НЕ ставь 🎉 за промежуточный результат, за один из шагов, за «почти верно» или за ответ на твой вопрос, если задача ещё не решена. За верный промежуточный шаг хвали БЕЗ 🎉 и сразу веди к следующему шагу (например: «Верно! 👍 Теперь…»). Эмодзи 🎉 и слово «Правильно! Ответ:» — это сигнал, что ВСЯ задача решена; используй их один раз в самом конце.

━━━ РАБОТА С ФОТО ━━━
• Тебе видны все фото диалога; можешь сравнивать их и ссылаться на предыдущие.
• Разбираешь решение с фото: проверь по шагам, укажи где ошибка, объясни, похвали за верное.
• Несколько фото — смотри по порядку; одна задача на нескольких фото — разбирай вместе; разные задачи — спроси, с какой начать; скажи, что видишь («Вижу 3 фото с задачами…»).
• Почерк неразборчивый — попроси переписать текстом или сфотографировать чётче.
• Просят «проверь»/«правильно?» — не задавай новые вопросы, именно проверь решение и скажи, верно оно или нет.

━━━ ОХВАТ: ЛЮБАЯ ЗАДАЧА, ЛЮБОЙ КЛАСС (1–11) ━━━
Ты помогаешь со ВСЕЙ школьной математикой:
• Арифметика: дроби, проценты, степени, корни, отрицательные числа, модуль.
• Алгебра: уравнения и неравенства (линейные, квадратные, кубические, системы, показательные, логарифмические), функции и графики, прогрессии, производные, интегралы, пределы, комбинаторика, вероятность, статистика, матрицы, комплексные числа.
• Геометрия: планиметрия, стереометрия, тригонометрия, векторы, координаты.
• Подготовка к ВПР, ОГЭ, ЕГЭ (база и профиль), контрольным и олимпиадам — это те же школьные темы.
НИКОГДА не отказывайся: нельзя говорить «я не могу это решить», «слишком сложно», «это не проходят в твоём классе». Любую задачу, даже олимпиадную, разбивай на маленькие шаги. Если задача выше или ниже класса ученика — всё равно помогай, подстроив объяснение под его уровень.
Если в задаче по физике или химии нужно что-то ПОСЧИТАТЬ — помоги с математической/вычислительной частью так же, через наводящие вопросы.
Условие непонятно, неполное или с опечаткой — не отказывайся: попроси уточнить/переснять и предложи наиболее вероятное прочтение.

━━━ ОБЪЯСНЕНИЕ ТЕМЫ С НУЛЯ (если «не понимаю тему», «объясни…») ━━━
Сначала выясни класс и что именно непонятно. Затем объясняй от простого к сложному и ОЧЕНЬ подробно:
1) что это такое — простыми словами и с жизненным примером (пицца, конфеты, деньги);
2) зачем нужно — где встречается в жизни;
3) как с этим работать — правила;
4) 3–5 примеров с разбором, после каждого — аналогичный вопрос ученику;
5) типичные ошибки и на чём путаются.
После каждого объяснения — проверочный вопрос. Хвали за правильные ответы.

━━━ СТИЛЬ ОБЩЕНИЯ ━━━
• Говори просто, как друг; дружелюбный тон и эмодзи (😊 👍 🎯 👏 🎉 🔍 🍕 🍎).
• Сначала пример из жизни, потом правило. Визуализируй (предметы, рисунки словами, пальцы для младших).
• Поддерживай: «Верно! 👍», «Точно! 🎯», «Молодец! 👏», «Ничего страшного, давай разберёмся».
• По классам: 1–4 — только простые слова и предметы (яблоки, конфеты), счёт на пальцах; 5–8 — логика действий и «зачем это нужно»; 9–11 — суть правил, закономерности, практические аналогии.
• Термины используй правильно (слагаемое/сумма, уменьшаемое/вычитаемое/разность, множитель/произведение, делимое/делитель/частное, числитель/знаменатель, степень, корень, гипотенуза/катеты), но для младших — простыми словами, термин в скобках.

━━━ ФОРМАТ ЗАПИСИ (важно!) ━━━
НЕ используй LaTeX и математическую разметку: никаких знаков доллара вокруг формул, команд с обратным слэшем (вроде frac) и степеней через фигурные скобки.
Пиши ОБЫЧНЫМ текстом и юникодом: x², x³, 1/2, (a+b)/c, √16, ∛8, 2 · 3, 24 ÷ 4.

━━━ ВНУТРЕННИЙ САМОКОНТРОЛЬ (только для тебя, ученику НЕ показывай и НЕ присылай) ━━━
Про себя, молча: определи тип задачи → составь план → реши по шагам → проверь ответ подстановкой → запомни правильный ответ. Он нужен тебе только чтобы понимать, куда вести ученика и верно ли он в итоге ответил. Ученику ты выдаёшь не готовое решение, а наводящие вопросы. «🎉 Правильно! Ответ: …» — только после того, как ученик САМ назовёт верный ответ.

Твоя цель: чтобы ученик САМ дошёл до решения и ПОНЯЛ тему. Объясняй так просто, чтобы понял даже первоклассник. 🎒"""
    
    async def get_teaching_response(
        self,
        task_text: str,
        class_number: int,
        conversation_history: list = None,
        image_url: str = None,
        image_urls: list = None
    ) -> str:
        """
        Получить обучающий ответ от AI
        
        Args:
            task_text: Текст задачи или сообщение ученика
            class_number: Класс ученика
            conversation_history: История диалога
            image_url: URL изображения с задачей (опционально, устаревший параметр)
            image_urls: Список URL изображений (новый параметр для множественных фото)
            
        Returns:
            Ответ AI
        """
        messages = [
            {"role": "system", "content": self.system_prompt + f"\n\nУченик: {class_number} класс"}
        ]
        
        # Поддержка обратной совместимости
        if image_url and not image_urls:
            image_urls = [image_url]
        
        # Если есть история - используем её
        if conversation_history and len(conversation_history) > 0:
            # Нормализация и валидация истории перед отправкой в API
            validated_history = []
            for idx, msg in enumerate(conversation_history):
                if not isinstance(msg, dict):
                    from utils import setup_logger
                    logger = setup_logger()
                    logger.error(f"❌ VALIDATION ERROR: message[{idx}] не dict: {type(msg)} = {msg}")
                    continue
                
                role = msg.get("role")
                content = msg.get("content")
                
                # КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Нормализуем content к правильному формату
                if isinstance(content, list):
                    # У нас уже массив - проверяем что все элементы корректны
                    validated_content = []
                    for item_idx, item in enumerate(content):
                        if isinstance(item, dict):
                            # Проверяем что это правильный объект
                            if item.get("type") == "text":
                                # Убеждаемся что text - строка
                                text_value = item.get("text", "")
                                if not isinstance(text_value, str):
                                    from utils import setup_logger
                                    logger = setup_logger()
                                    logger.error(f"❌ VALIDATION ERROR: message[{idx}].content[{item_idx}].text не string: {type(text_value)} = {text_value}")
                                    text_value = str(text_value)
                                validated_content.append({"type": "text", "text": text_value})
                            elif item.get("type") == "image_url":
                                # Изображение - просто копируем
                                validated_content.append(item)
                            else:
                                from utils import setup_logger
                                logger = setup_logger()
                                logger.warning(f"⚠️ VALIDATION WARNING: message[{idx}].content[{item_idx}] имеет неизвестный type: {item.get('type')}")
                        elif isinstance(item, str):
                            # Строка в массиве - это ошибка! Нужен объект
                            from utils import setup_logger
                            logger = setup_logger()
                            logger.error(f"❌ VALIDATION ERROR: message[{idx}].content[{item_idx}] строка в массиве (нужен объект): {item[:100]}...")
                            # Конвертируем строку в правильный формат
                            validated_content.append({"type": "text", "text": str(item)})
                        else:
                            from utils import setup_logger
                            logger = setup_logger()
                            logger.error(f"❌ VALIDATION ERROR: message[{idx}].content[{item_idx}] неизвестный тип: {type(item)}")
                    
                    if validated_content:
                        validated_history.append({"role": role, "content": validated_content})
                    else:
                        from utils import setup_logger
                        logger = setup_logger()
                        logger.error(f"❌ VALIDATION ERROR: message[{idx}] имеет пустой content после валидации")
                
                elif isinstance(content, str):
                    # Строка - это валидный формат для OpenAI API
                    validated_history.append({"role": role, "content": content})
                else:
                    from utils import setup_logger
                    logger = setup_logger()
                    logger.error(f"❌ VALIDATION ERROR: message[{idx}].content имеет неверный тип: {type(content)} = {content}")
            
            # История уже содержит все сообщения включая текущее
            messages.extend(validated_history)
        else:
            # Первое сообщение - это задача
            # Если есть изображения - добавляем их
            if image_urls:
                user_message_content = []
                # Добавляем все изображения
                for img_url in image_urls:
                    user_message_content.append({
                        "type": "image_url",
                        "image_url": {"url": img_url}
                    })
                # Добавляем текст
                photo_text = "фото" if len(image_urls) == 1 else f"{len(image_urls)} фото"
                user_message_content.append({
                    "type": "text",
                    "text": f"На {photo_text} задача. {task_text}\n\nВнимательно изучи задачу на изображении и задавай мне наводящие вопросы, не давай готовое решение!"
                })
            else:
                user_message_content = f"Помоги мне решить эту задачу:\n\n{task_text}\n\nЗадавай мне наводящие вопросы, не давай готовое решение!"
            
            messages.append({
                "role": "user",
                "content": user_message_content
            })
        
        try:
            # Проверяем есть ли изображение в первом сообщении истории
            has_image = False
            image_count = 0
            
            if conversation_history and len(conversation_history) > 0:
                first_user_msg = conversation_history[0]
                if first_user_msg.get("role") == "user":
                    content = first_user_msg.get("content")
                    # Проверяем формат с изображением (список с type: image_url)
                    if isinstance(content, list):
                        image_count = sum(1 for item in content if isinstance(item, dict) and item.get("type") == "image_url")
                        has_image = image_count > 0
            
            # Добавляем количество изображений из нового запроса
            if image_urls:
                image_count += len(image_urls)
            
            # ВАЖНО: OpenAI API имеет ограничения на количество изображений
            # Рекомендуется не больше 10-15 изображений за запрос
            # Если изображений больше - AI всё равно обработает, но может быть медленнее
            
            # Используем gpt-4o если есть изображение в истории или передано напрямую
            model = "gpt-4o" if (image_urls or has_image) else self.model
            
            # Отправляем запрос с авто-повтором при таймауте/обрыве связи
            # И при превышении лимита запросов OpenAI (ошибка 429, rate limit / TPM).
            # ВАЖНО: таймаут — это НЕ про баланс. Rate limit (429) — это тоже НЕ про баланс,
            # а про то, что за минуту отправлено слишком много токенов/запросов: достаточно
            # подождать несколько секунд и повторить, и ученик ошибки даже не увидит.
            response = None
            last_conn_error = None
            last_rate_error = None
            max_attempts = 5  # до 5 попыток (лимит токенов в минуту сбрасывается в пределах ~60 сек)
            MAX_TOKENS = 2000  # резерв под ответ (меньше токенов на запрос → реже упираемся в TPM)
            # Заранее оцениваем «вес» запроса, чтобы лимитер придержал его и мы НЕ упёрлись
            # в лимит токенов в минуту (это и есть главная защита от 429 — проактивная).
            estimated_tokens = self._estimate_request_tokens(messages, MAX_TOKENS)
            for attempt in range(max_attempts):
                # Применяем rate limiting ПО ТОКЕНАМ: ждём, пока в минутном бюджете хватит места
                reservation = await self.rate_limiter.acquire(estimated_tokens)
                try:
                    response = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            model=model,
                            messages=messages,
                            temperature=0.2,  # Низкая температура для точных и стабильных вычислений
                            max_tokens=MAX_TOKENS
                        ),
                        timeout=75.0  # запас над таймаутом клиента (60 сек)
                    )
                    # Фиксируем фактический расход токенов вместо оценки (точность лимитера)
                    try:
                        actual = response.usage.total_tokens if response.usage else estimated_tokens
                        self.rate_limiter.settle(reservation, actual)
                    except Exception:
                        pass
                    break
                except (asyncio.TimeoutError, APITimeoutError, APIConnectionError) as conn_err:
                    self.rate_limiter.cancel(reservation)  # запрос не состоялся — снимаем резерв
                    last_conn_error = conn_err
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(1)  # короткая пауза перед повтором
                    continue
                except RateLimitError as rate_err:
                    self.rate_limiter.cancel(reservation)  # запрос отклонён — токены не потрачены
                    rate_msg = str(rate_err)
                    # Нехватка средств / исчерпана квота — повторять бессмысленно,
                    # пробрасываем во внешний обработчик (покажет сообщение про баланс).
                    if "insufficient_quota" in rate_msg or "billing" in rate_msg.lower():
                        raise
                    # Иначе это лимит запросов/токенов в минуту — ждём и повторяем.
                    last_rate_error = rate_err
                    if attempt < max_attempts - 1:
                        wait_s = self._parse_retry_after(rate_err, attempt)
                        await asyncio.sleep(wait_s)
                    continue
                finally:
                    # Освобождаем slot одновременности в rate limiter
                    self.rate_limiter.release()

            if response is None:
                # Все попытки исчерпаны. Разбираемся, что именно случилось.
                if last_rate_error is not None:
                    # Упёрлись в лимит запросов OpenAI даже после нескольких повторов.
                    try:
                        from services.error_logger import log_error
                        asyncio.create_task(log_error(
                            error=last_rate_error,
                            context="AIService.get_teaching_response (лимит запросов OpenAI / 429 после повторов)",
                            message_text=f"model={model}, task={str(task_text)[:150]}"
                        ))
                    except Exception:
                        pass
                    return (
                        "⏳ Сейчас слишком много запросов к ИИ, он не успевает отвечать.\n\n"
                        "Это НЕ связано с балансом — просто временная нагрузка.\n\n"
                        "🔄 Подожди 10–15 секунд и отправь сообщение ещё раз.", 0
                    )
                # Иначе — таймаут/нет связи. Логируем РЕАЛЬНУЮ причину в канал.
                try:
                    from services.error_logger import log_error
                    asyncio.create_task(log_error(
                        error=last_conn_error or asyncio.TimeoutError("timeout"),
                        context="AIService.get_teaching_response (таймаут/нет связи с OpenAI)",
                        message_text=f"model={model}, task={str(task_text)[:150]}"
                    ))
                except Exception:
                    pass
                return (
                    "⏳ OpenAI не ответил вовремя.\n\n"
                    "Это НЕ связано с балансом — обычно это временные проблемы "
                    "с сетью или доступом к OpenAI.\n\n"
                    "🔄 Попробуй ещё раз через несколько секунд.", 0
                )

            # Подсчитываем использованные токены
            tokens_used = response.usage.total_tokens if response.usage else 0

            answer_text = response.choices[0].message.content

            # САМОПРОВЕРКА: если репетитор собирается подтвердить ответ ученика (🎉),
            # делаем независимую проверку вычислений отдельным запросом. Если ответ
            # ученика на самом деле неверный — НЕ подтверждаем, а мягко просим пересчитать.
            try:
                answer_text, verify_tokens = await self._verify_before_confirm(
                    answer_text, messages, model
                )
                tokens_used += verify_tokens
            except Exception as verify_err:
                # Самопроверка не должна ломать основной ответ — при любой ошибке
                # просто оставляем исходный ответ репетитора как есть.
                print(f"WARN: самопроверка не удалась: {verify_err}")

            return answer_text, tokens_used
        except Exception as e:
            error_msg = str(e)
            print(f"ERROR in get_teaching_response: {error_msg}")  # Для отладки
            
            # Логируем критичные ошибки в канал
            if not ("insufficient_quota" in error_msg or "billing" in error_msg.lower()):
                try:
                    from services.error_logger import log_error
                    # Создаём задачу для логирования (не блокируем)
                    asyncio.create_task(log_error(
                        error=e,
                        context="AIService.get_teaching_response",
                        message_text=f"Task: {task_text[:200]}..."
                    ))
                except Exception:
                    pass
            
            if "insufficient_quota" in error_msg or "billing" in error_msg.lower():
                return "❌ Недостаточно средств на балансе OpenAI\n\n💳 Пополните баланс: https://platform.openai.com/account/billing\n\n🔄 После пополнения попробуйте снова", 0
            elif "invalid_api_key" in error_msg or "authentication" in error_msg.lower():
                return "❌ Неверный API ключ OpenAI\n\n🔑 Обратитесь к администратору", 0
            else:
                return f"❌ Произошла ошибка при обработке запроса.\n\nДетали: {error_msg}\n\nПопробуй переформулировать задачу.", 0

    @staticmethod
    def _estimate_request_tokens(messages: list, max_tokens: int) -> int:
        """
        Консервативная оценка, сколько токенов «съест» запрос (prompt + резерв под ответ).

        Нужна, чтобы лимитер заранее придержал запрос и мы НЕ упёрлись в лимит TPM
        (ошибка 429). Намеренно слегка ЗАВЫШАЕМ оценку — это безопасная сторона:
        лучше чуть лишний раз подождать, чем словить 429. После ответа фактический
        расход доучитывается через rate_limiter.add_tokens().
        """
        text_chars = 0
        images = 0
        for m in messages or []:
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, str):
                text_chars += len(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            text_chars += len(item.get("text") or "")
                        elif item.get("type") == "image_url":
                            images += 1
        # ~2.3 символа на токен для русского текста (с запасом) + ~2000 токенов на фото
        # (фото — главная неопределённость в оценке, берём с запасом в безопасную сторону).
        text_tokens = int(text_chars / 2.3) + 1
        image_tokens = images * 2000
        return text_tokens + image_tokens + int(max_tokens)

    def _parse_retry_after(self, rate_err, attempt: int = 0) -> float:
        """
        Сколько секунд подождать перед повтором после ошибки 429 (rate limit).

        Берёт время из заголовков ответа OpenAI (retry-after / retry-after-ms),
        затем из текста ошибки ("Please try again in 7.354s"), иначе — нарастающая
        запасная задержка. Результат ограничен диапазоном [1; 30] секунд.
        """
        # 1) Заголовки HTTP-ответа
        try:
            resp = getattr(rate_err, "response", None)
            headers = getattr(resp, "headers", None) or {}
            ra_ms = headers.get("retry-after-ms") or headers.get("Retry-After-Ms")
            if ra_ms:
                return min(max(float(ra_ms) / 1000.0 + 0.5, 1.0), 30.0)
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra:
                return min(max(float(ra) + 0.5, 1.0), 30.0)
        except Exception:
            pass
        # 2) Текст ошибки: "try again in 7.354s" или "try again in 500ms"
        try:
            msg = str(rate_err)
            m_ms = re.search(r"try again in ([\d.]+)\s*ms", msg)
            if m_ms:
                return min(max(float(m_ms.group(1)) / 1000.0 + 0.5, 1.0), 30.0)
            m_s = re.search(r"try again in ([\d.]+)\s*s", msg)
            if m_s:
                return min(max(float(m_s.group(1)) + 0.5, 1.0), 30.0)
        except Exception:
            pass
        # 3) Запасной вариант: нарастающая задержка 4, 8, 12... сек
        return min(4.0 * (attempt + 1), 20.0)

    @staticmethod
    def is_service_message(text: str) -> bool:
        """
        Это служебное сообщение (ошибка / лимит / нет связи), а НЕ обучающий ответ репетитора?

        Такие сообщения формирует get_teaching_response при сбоях и всегда НАЧИНАЮТСЯ
        с ❌ / ⚠️ / ⏳. Важно отличать их от обычного ответа ИИ: репетитор по инструкции
        МОЖЕТ использовать ❌/⚠️ ВНУТРИ текста (например, разбирая ошибку ученика:
        «24 + 3 = 30 ❌ — тут ошибка»), но никогда не начинает ответ с этих символов.
        Поэтому проверяем именно начало строки, а не вхождение где угодно.
        """
        if not text:
            return False
        return text.lstrip().startswith(("❌", "⚠️", "⏳"))

    # Фразы, которыми репетитор подтверждает ПРАВИЛЬНЫЙ финальный ответ ученика.
    # Широкий список, т.к. формулировка ИИ варьируется. Используется и самопроверкой,
    # и завершением занятия (handlers/student.py) — ЕДИНЫЙ источник через
    # is_success_confirmation(), поэтому списки не расходятся.
    _CONFIRM_PHRASES = (
        "правильно! ответ:",
        "верно! ответ:",
        "молодец! ответ:",
        "точно! ответ:",
        "правильный ответ:",
        "это правильный ответ",
        "ты получил правильный ответ",
        "ответ верный",
        "ответ правильный",
        "твой ответ верный",
        "твой ответ правильный",
        "ты всё правильно",
        "ты все правильно",
        "ты всё верно",
        "ты все верно",
        "всё правильно!",
        "все правильно!",
        "всё верно!",
        "все верно!",
        "ты справился",
        "правильно решил",
        "верно решил",
        "решил верно",
        "решил правильно",
        "сам дошёл",
        "сам дошел",
        "задача решена",
    )

    @classmethod
    def is_success_confirmation(cls, text: str) -> bool:
        """
        Репетитор подтверждает ПРАВИЛЬНЫЙ финальный ответ ученика? (🎉 + подтверждающая фраза)

        ЕДИНЫЙ детектор «задача решена верно» — им пользуются и самопроверка внутри
        AIService, и завершение занятия в handlers/student.py. Благодаря этому задача,
        которую ученик реально решил, стабильно записывается как ПРАВИЛЬНАЯ, а не как
        ошибка (раньше списки фраз в двух местах расходились).
        """
        if not text or "🎉" not in text:
            return False
        low = text.lower()
        return any(phrase in low for phrase in cls._CONFIRM_PHRASES)

    def _looks_like_confirmation(self, answer_text: str) -> bool:
        """Похоже ли, что репетитор подтверждает финальный ответ ученика (🎉 + фраза)."""
        return self.is_success_confirmation(answer_text)

    async def _verify_before_confirm(self, answer_text: str, messages: list, model: str):
        """
        Независимая самопроверка перед подтверждением ответа ученика.

        Если репетитор собирается сказать «🎉 Правильно! Ответ: ...», мы отдельным
        запросом (строгий проверяющий, температура 0) заново решаем задачу и сверяем
        с ответом ученика. Если ответ ученика на самом деле неверный — заменяем
        подтверждение на мягкую просьбу пересчитать (без 🎉), чтобы бот не закрывал
        задачу с ошибкой.

        Returns:
            (answer_text, verify_tokens) — исходный или заменённый ответ и расход токенов.
        """
        # Проверяем только настоящие подтверждения финального ответа.
        if not self._looks_like_confirmation(answer_text):
            return answer_text, 0

        # Нужен контекст: сама задача + ответы ученика.
        history_ctx = messages[1:] if len(messages) > 1 else []
        if len(history_ctx) < 2:
            return answer_text, 0

        # Ограничиваем контекст, чтобы не раздувать токены: условие задачи
        # (первое сообщение) + последние несколько реплик диалога.
        if len(history_ctx) > 8:
            history_ctx = [history_ctx[0]] + history_ctx[-7:]

        verifier_system = (
            "Ты — строгий и очень внимательный проверяющий по школьной математике. "
            "Выше — диалог репетитора с учеником (возможно, с фото задачи). "
            "Определи ИСХОДНУЮ задачу и ПОСЛЕДНИЙ итоговый ответ, который назвал УЧЕНИК, "
            "затем самостоятельно реши задачу с нуля, аккуратно посчитай и перепроверь, и "
            "сравни свой результат с ответом ученика. Также оцени, является ли ответ ученика "
            "ОКОНЧАТЕЛЬНЫМ ответом на ВСЮ исходную задачу, а не промежуточным шагом.\n\n"
            "Отвечай ТОЛЬКО одним объектом JSON, без пояснений и текста вокруг, в формате:\n"
            '{"is_correct": true|false, "is_final_answer": true|false, '
            '"correct_answer": "<твой правильный ответ>", '
            '"nudge": "<один короткий наводящий вопрос ученику, НЕ раскрывающий правильный ответ>"}\n\n'
            "ПРАВИЛА:\n"
            "- is_correct = false ставь ТОЛЬКО если уверен, что ответ ученика неверный. Если не уверен, "
            "или задача без однозначного числового ответа — ставь is_correct = true.\n"
            "- is_final_answer = false ставь ТОЛЬКО если уверен, что ученик назвал лишь ПРОМЕЖУТОЧНЫЙ "
            "результат (значение одного из шагов), а исходная задача требует ещё действий для ответа. "
            "Если ответ ученика закрывает всю задачу, или если ты не уверен — ставь is_final_answer = true.\n"
            "- Поле nudge заполняй, когда is_correct = false ИЛИ is_final_answer = false: это один "
            "короткий наводящий вопрос к следующему шагу, БЕЗ раскрытия правильного ответа."
        )

        verify_messages = [{"role": "system", "content": verifier_system}]
        verify_messages.extend(history_ctx)
        verify_messages.append({
            "role": "user",
            "content": (
                "Проверь: (1) верен ли последний итоговый ответ ученика и (2) является ли он "
                "ОКОНЧАТЕЛЬНЫМ ответом на всю задачу. Верни результат только в описанном JSON-формате."
            )
        })

        # Делаем до 2 попыток: самопроверка — это защита от неверного подтверждения,
        # поэтому не хотим терять её из-за одной мимолётной ошибки лимита (429).
        verify_tokens = 0
        verify_resp = None
        verify_estimate = self._estimate_request_tokens(verify_messages, 500)
        for v_attempt in range(2):
            # Проверочный запрос тоже проходит через лимитер токенов, чтобы не вызвать 429
            v_reservation = await self.rate_limiter.acquire(verify_estimate)
            try:
                verify_resp = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model="gpt-4o",  # надёжен в вычислениях и видит фото
                        messages=verify_messages,
                        temperature=0,
                        max_tokens=500,
                        response_format={"type": "json_object"},
                    ),
                    timeout=60.0,
                )
                try:
                    v_actual = verify_resp.usage.total_tokens if verify_resp.usage else verify_estimate
                    self.rate_limiter.settle(v_reservation, v_actual)
                except Exception:
                    pass
                break
            except RateLimitError as v_rate:
                self.rate_limiter.cancel(v_reservation)
                if "insufficient_quota" in str(v_rate) or "billing" in str(v_rate).lower():
                    break  # нет смысла повторять — выходим и оставляем ответ как есть
                if v_attempt == 0:
                    await asyncio.sleep(self._parse_retry_after(v_rate, v_attempt))
                continue
            except (asyncio.TimeoutError, APITimeoutError, APIConnectionError):
                self.rate_limiter.cancel(v_reservation)
                if v_attempt == 0:
                    await asyncio.sleep(1)
                continue
            finally:
                self.rate_limiter.release()

        if verify_resp is None:
            # Самопроверку сделать не удалось — не рискуем, оставляем ответ репетитора как есть.
            return answer_text, verify_tokens

        if verify_resp.usage:
            verify_tokens = verify_resp.usage.total_tokens

        raw = verify_resp.choices[0].message.content or ""
        verdict = self._parse_verdict(raw)
        if verdict is None:
            # Не смогли разобрать вердикт — не рискуем, оставляем ответ как есть.
            return answer_text, verify_tokens

        nudge = (verdict.get("nudge") or "").strip()

        # 1) Ответ НЕВЕРНЫЙ → не подтверждаем, мягко просим пересчитать (без 🎉).
        if verdict.get("is_correct") is False:
            corrected = (
                "Хм, давай ещё раз перепроверим 🧐 Кажется, в вычислениях закралась "
                "маленькая неточность."
            )
            if nudge:
                corrected += f"\n\n{nudge}"
            corrected += "\n\nПосчитай, пожалуйста, внимательно ещё разок 👍"
            return corrected, verify_tokens

        # 2) Ответ ВЕРНЫЙ, но это лишь промежуточный шаг, а не финал всей задачи →
        #    НЕ закрываем задачу раньше времени: убираем 🎉, хвалим шаг и ведём дальше.
        if verdict.get("is_final_answer") is False:
            praise = "Верно! 👍 Это правильный шаг, но задача ещё не решена до конца."
            if nudge:
                praise += f"\n\n{nudge}"
            else:
                praise += "\n\nДавай доведём решение до конца — сделай следующий шаг."
            return praise, verify_tokens

        # 3) Ответ верный И финальный → оставляем подтверждение репетитора как есть.
        return answer_text, verify_tokens

    def _parse_verdict(self, raw: str):
        """Аккуратно извлекает JSON-вердикт проверяющего. Возвращает dict или None."""
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            pass
        # На случай, если вокруг JSON оказался лишний текст — вырезаем {...}.
        try:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:
            pass
        return None
