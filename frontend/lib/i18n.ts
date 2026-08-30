/**
 * Interface copy in the three languages the market actually reads.
 *
 * Azerbaijani is the default because the market is Azerbaijani; Russian and
 * English follow. The dictionary is a plain object rather than a runtime
 * library: every key is resolved at build time, a missing translation is a
 * TypeScript error rather than a string that silently falls back to a key, and
 * the whole thing costs nothing to ship.
 *
 * Report bodies are not translated here. They are generated server-side from
 * computed evidence and carry their own language, so translating the chrome
 * around them would be misleading if the report itself stayed English. The
 * analysis request already takes a language argument.
 */

export const LOCALES = ["az", "ru", "en"] as const;
export type Locale = (typeof LOCALES)[number];

export const LOCALE_NAMES: Record<Locale, string> = {
  az: "Azərbaycan",
  ru: "Русский",
  en: "English",
};

/** Short label for the switcher, where the full name does not fit. */
export const LOCALE_SHORT: Record<Locale, string> = {
  az: "AZ",
  ru: "RU",
  en: "EN",
};

export const DEFAULT_LOCALE: Locale = "az";

export function isLocale(value: string | null | undefined): value is Locale {
  return !!value && (LOCALES as readonly string[]).includes(value);
}

type Dictionary = {
  brand: string;
  tagline: { lead: string; emphasis: string };
  intro: string;
  nav: {
    analyse: string;
    discover: string;
    deals: string;
    chat: string;
    saved: string;
    profile: string;
  };
  navHint: Record<"analyse" | "discover" | "deals" | "chat" | "saved" | "profile", string>;
  actions: {
    print: string;
    buy: string;
    consult: string;
    signIn: string;
    language: string;
  };
  states: {
    analysing: string;
    failed: string;
    notBuiltTitle: string;
    needsAccount: string;
    needsKey: string;
  };
  auth: {
    signInTitle: string;
    signInLead: string;
    registerTitle: string;
    registerLead: string;
    email: string;
    password: string;
    passwordHint: string;
    firstName: string;
    lastName: string;
    birthYear: string;
    birthYearHint: string;
    optional: string;
    submitSignIn: string;
    submitRegister: string;
    toRegister: string;
    toSignIn: string;
    working: string;
  };
  profile: {
    title: string;
    signedInAs: string;
    plan: string;
    save: string;
    saved: string;
    signOut: string;
    dataNote: string;
  };
  saved: {
    empty: string;
    emptyHint: string;
    save: string;
    saving: string;
    alreadySaved: string;
    remove: string;
    targetPrice: string;
    savedOn: string;
    signInFirst: string;
  };
  finds: {
    belowMedian: string;
    basedOn: string;
    listings: string;
    mileageAbove: string;
    mileageBelow: string;
    mileageUnknown: string;
    empty: string;
    emptyHint: string;
    analyse: string;
  };
  discover: {
    budgetTitle: string;
    inferred: string;
    stated: string;
    basedOnObservations: string;
    needMore: string;
    setRange: string;
    from: string;
    to: string;
    apply: string;
    reset: string;
    signInOrSet: string;
    noMatches: string;
    atOrBelow: string;
  };
  disclaimer: string;
};

const az: Dictionary = {
  brand: "AutoIntel Azərbaycan",
  tagline: { lead: "Maşını tanı. Bazarı tanı.", emphasis: "Qərarı özün ver." },
  intro:
    "Avtomobili daxil edin — onu yerli bazardakı elanlarla müqayisə edir, dəyərini " +
    "qiymətləndirir, istənilən qiymətin harada dayandığını göstərir və sübutun nəyi " +
    "təsdiqləyib nəyi təsdiqləmədiyini açıq yazırıq. Hər rəqəm bazar datasından " +
    "hesablanır və necə alındığı göstərilir. Qərar sizdə qalır.",
  nav: {
    analyse: "Analiz",
    discover: "Kəşf et",
    deals: "Bu günün tapıntıları",
    chat: "Mütəxəssis",
    saved: "Seçilmişlər",
    profile: "Profil",
  },
  navHint: {
    analyse: "Bir avtomobili bazarla müqayisə edin",
    discover: "Büdcənizə uyğun avtomobillər",
    deals: "Hesablanmış aralıqdan aşağı qiymətlənənlər",
    chat: "Bazar haqqında sual verin",
    saved: "Yadda saxladığınız avtomobillər",
    profile: "Hesabınız və tərcihləriniz",
  },
  actions: {
    print: "Çap et və ya PDF kimi saxla",
    buy: "Elana keç",
    consult: "Mütəxəssislə məsləhətləş",
    signIn: "Daxil ol",
    language: "Dil",
  },
  states: {
    analysing: "Müqayisələr seçilir, bazar modeli qurulur, risk göstəriciləri yoxlanılır…",
    failed: "Analizi tamamlamaq mümkün olmadı",
    notBuiltTitle: "Bu bölmə hələ hazır deyil",
    needsAccount: "Bu bölmə hesab tələb edir. Hesab sistemi hazırlanır.",
    needsKey: "Söhbət üçün AI açarı təyin olunmayıb, ona görə hazırda cavab verə bilmir.",
  },
  auth: {
    signInTitle: "Hesabınıza daxil olun",
    signInLead: "Seçilmişlər, profil və sizə uyğunlaşdırılmış tövsiyələr üçün.",
    registerTitle: "Hesab yaradın",
    registerLead: "Yalnız sizə uyğun cavab vermək üçün lazım olanı soruşuruq.",
    email: "E-poçt",
    password: "Parol",
    passwordHint: "Ən azı 10 simvol.",
    firstName: "Ad",
    lastName: "Soyad",
    birthYear: "Doğum ili",
    birthYearHint: "Yaş əvəzinə il soruşuruq — o, köhnəlmir.",
    optional: "istəyə bağlı",
    submitSignIn: "Daxil ol",
    submitRegister: "Hesab yarat",
    toRegister: "Hesabınız yoxdur? Yaradın",
    toSignIn: "Hesabınız var? Daxil olun",
    working: "Gözləyin…",
  },
  profile: {
    title: "Profil",
    signedInAs: "Daxil olmusunuz:",
    plan: "Tarif",
    save: "Yadda saxla",
    saved: "Saxlanıldı",
    signOut: "Çıxış",
    dataNote:
      "Bu məlumatlar yalnız cavabları sizə uyğunlaşdırmaq üçündür. Doğum ili yaş əvəzinə saxlanılır, tam doğum tarixi soruşulmur.",
  },
  saved: {
    empty: "Hələ heç nə saxlamamısınız.",
    emptyHint: "Bir avtomobili analiz edin və hesabatın altındakı düymə ilə buraya əlavə edin.",
    save: "Seçilmişlərə əlavə et",
    saving: "Əlavə olunur…",
    alreadySaved: "Siyahınızdadır",
    remove: "Sil",
    targetPrice: "Hədəf qiymət",
    savedOn: "Əlavə olunub",
    signInFirst: "Saxlamaq üçün daxil olun.",
  },
  finds: {
    belowMedian: "medyandan aşağı",
    basedOn: "əsas:",
    listings: "elan",
    mileageAbove: "yürüş medyandan yuxarı",
    mileageBelow: "yürüş medyandan aşağı",
    mileageUnknown: "yürüş bilinmir",
    empty: "Hazırda tapıntı yoxdur.",
    emptyHint: "Bazar anlıq görüntüləri hələ qurulmayıb və ya heç bir elan meyarlara uyğun gəlmir.",
    analyse: "Tam analiz",
  },
  discover: {
    budgetTitle: "Büdcəniz",
    inferred: "təxmin edilib",
    stated: "sizin təyin etdiyiniz",
    basedOnObservations: "baxdığınız avtomobilə əsaslanır",
    needMore: "Büdcəni təxmin etmək üçün ən azı {n} avtomobil lazımdır. Nəzərdə tutduğunuz aralıqda bir neçəsini analiz edin və ya aralığı özünüz yazın.",
    setRange: "Aralığı özünüz təyin edin",
    from: "-dan",
    to: "-a qədər",
    apply: "Tətbiq et",
    reset: "Təxminə qayıt",
    signInOrSet: "Daxil olun, və ya aralığı özünüz yazın.",
    noMatches: "Bu aralıqda uyğun avtomobil tapılmadı.",
    atOrBelow: "öz bazarından aşağı",
  },
  disclaimer:
    "AutoIntel bazar-kəşfiyyat alətidir. Heç bir avtomobilin texniki vəziyyətinə, " +
    "qəza tarixçəsinə, hüquqi statusuna və ya gələcək etibarlılığına zəmanət vermir " +
    "və müstəqil ekspertizanı əvəz etmir.",
};

const ru: Dictionary = {
  brand: "AutoIntel Азербайджан",
  tagline: { lead: "Знай машину. Знай рынок.", emphasis: "Решай сам." },
  intro:
    "Укажите автомобиль — мы сравним его с объявлениями на местном рынке, оценим " +
    "стоимость, покажем, где находится запрашиваемая цена, и прямо изложим, что " +
    "данные подтверждают, а что нет. Каждая цифра рассчитана из рыночных данных, " +
    "и показано, как она получена. Решение остаётся за вами.",
  nav: {
    analyse: "Анализ",
    discover: "Подбор",
    deals: "Находки дня",
    chat: "Эксперт",
    saved: "Избранное",
    profile: "Профиль",
  },
  navHint: {
    analyse: "Сравнить автомобиль с рынком",
    discover: "Автомобили под ваш бюджет",
    deals: "Оценённые ниже расчётного диапазона",
    chat: "Задать вопрос о рынке",
    saved: "Сохранённые автомобили",
    profile: "Аккаунт и предпочтения",
  },
  actions: {
    print: "Печать или сохранить в PDF",
    buy: "Перейти к объявлению",
    consult: "Обсудить с экспертом",
    signIn: "Войти",
    language: "Язык",
  },
  states: {
    analysing: "Подбираем аналоги, строим модель рынка, проверяем индикаторы риска…",
    failed: "Не удалось выполнить анализ",
    notBuiltTitle: "Этот раздел ещё не готов",
    needsAccount: "Раздел требует аккаунта. Система аккаунтов в разработке.",
    needsKey: "Ключ ИИ не задан, поэтому чат сейчас не отвечает.",
  },
  auth: {
    signInTitle: "Вход в аккаунт",
    signInLead: "Для избранного, профиля и подобранных под вас рекомендаций.",
    registerTitle: "Создать аккаунт",
    registerLead: "Спрашиваем только то, что нужно, чтобы отвечать именно вам.",
    email: "Эл. почта",
    password: "Пароль",
    passwordHint: "Не менее 10 символов.",
    firstName: "Имя",
    lastName: "Фамилия",
    birthYear: "Год рождения",
    birthYearHint: "Спрашиваем год, а не возраст — он не устаревает.",
    optional: "необязательно",
    submitSignIn: "Войти",
    submitRegister: "Создать аккаунт",
    toRegister: "Нет аккаунта? Создайте",
    toSignIn: "Уже есть аккаунт? Войдите",
    working: "Подождите…",
  },
  profile: {
    title: "Профиль",
    signedInAs: "Вы вошли как:",
    plan: "Тариф",
    save: "Сохранить",
    saved: "Сохранено",
    signOut: "Выйти",
    dataNote:
      "Эти данные нужны только для того, чтобы отвечать вам точнее. Храним год рождения вместо возраста и не спрашиваем полную дату.",
  },
  saved: {
    empty: "Вы пока ничего не сохранили.",
    emptyHint: "Проанализируйте автомобиль и добавьте его кнопкой под отчётом.",
    save: "В избранное",
    saving: "Добавляем…",
    alreadySaved: "Уже в списке",
    remove: "Удалить",
    targetPrice: "Целевая цена",
    savedOn: "Добавлено",
    signInFirst: "Войдите, чтобы сохранять.",
  },
  finds: {
    belowMedian: "ниже медианы",
    basedOn: "основано на",
    listings: "объявл.",
    mileageAbove: "пробег выше медианы",
    mileageBelow: "пробег ниже медианы",
    mileageUnknown: "пробег неизвестен",
    empty: "Сейчас находок нет.",
    emptyHint: "Срезы рынка ещё не построены или ни одно объявление не проходит порог.",
    analyse: "Полный анализ",
  },
  discover: {
    budgetTitle: "Ваш бюджет",
    inferred: "оценён",
    stated: "задан вами",
    basedOnObservations: "по просмотренным автомобилям",
    needMore: "Для оценки нужно хотя бы {n} автомобиля. Проанализируйте несколько в нужном диапазоне или задайте его сами.",
    setRange: "Задать диапазон",
    from: "от",
    to: "до",
    apply: "Применить",
    reset: "Вернуть оценку",
    signInOrSet: "Войдите или задайте диапазон сами.",
    noMatches: "В этом диапазоне ничего не найдено.",
    atOrBelow: "ниже своего рынка",
  },
  disclaimer:
    "AutoIntel — инструмент рыночной аналитики. Он не гарантирует техническое " +
    "состояние, историю аварий, юридический статус или дальнейшую надёжность " +
    "автомобиля и не заменяет независимую экспертизу.",
};

const en: Dictionary = {
  brand: "AutoIntel Azerbaijan",
  tagline: { lead: "Know the car. Know the market.", emphasis: "Decide yourself." },
  intro:
    "Enter a vehicle and we will compare it against listings in the local market, " +
    "estimate what it is worth, show where the asking price sits, and set out what " +
    "the evidence does and does not establish. Every figure is computed from market " +
    "data and shown with its derivation. The decision stays with you.",
  nav: {
    analyse: "Analyse",
    discover: "Discover",
    deals: "Today's finds",
    chat: "Expert",
    saved: "Saved",
    profile: "Profile",
  },
  navHint: {
    analyse: "Compare one vehicle against the market",
    discover: "Vehicles that fit your budget",
    deals: "Priced below their computed range",
    chat: "Ask about the market",
    saved: "Vehicles you kept",
    profile: "Your account and preferences",
  },
  actions: {
    print: "Print or save as PDF",
    buy: "Go to the listing",
    consult: "Discuss with the expert",
    signIn: "Sign in",
    language: "Language",
  },
  states: {
    analysing: "Selecting comparables, fitting the market model, checking risk indicators…",
    failed: "Could not complete the analysis",
    notBuiltTitle: "This section is not built yet",
    needsAccount: "This section needs an account. Accounts are being built.",
    needsKey: "No AI key is configured, so the expert cannot answer yet.",
  },
  auth: {
    signInTitle: "Sign in",
    signInLead: "For saved vehicles, your profile and advice tuned to you.",
    registerTitle: "Create an account",
    registerLead: "We ask only for what it takes to answer you rather than anyone.",
    email: "Email",
    password: "Password",
    passwordHint: "At least 10 characters.",
    firstName: "First name",
    lastName: "Last name",
    birthYear: "Year of birth",
    birthYearHint: "A year rather than an age, because a year does not go stale.",
    optional: "optional",
    submitSignIn: "Sign in",
    submitRegister: "Create account",
    toRegister: "No account? Create one",
    toSignIn: "Already have an account? Sign in",
    working: "Working…",
  },
  profile: {
    title: "Profile",
    signedInAs: "Signed in as",
    plan: "Plan",
    save: "Save",
    saved: "Saved",
    signOut: "Sign out",
    dataNote:
      "These details are only used to answer you more precisely. We keep a year of birth rather than an age, and do not ask for a full date.",
  },
  saved: {
    empty: "Nothing saved yet.",
    emptyHint: "Analyse a vehicle and add it with the button under the report.",
    save: "Save this vehicle",
    saving: "Saving…",
    alreadySaved: "In your list",
    remove: "Remove",
    targetPrice: "Target price",
    savedOn: "Saved",
    signInFirst: "Sign in to save vehicles.",
  },
  finds: {
    belowMedian: "below the median",
    basedOn: "based on",
    listings: "listings",
    mileageAbove: "mileage above the median",
    mileageBelow: "mileage below the median",
    mileageUnknown: "mileage unknown",
    empty: "No finds right now.",
    emptyHint: "Market snapshots have not been built yet, or nothing clears the threshold.",
    analyse: "Full analysis",
  },
  discover: {
    budgetTitle: "Your budget",
    inferred: "estimated",
    stated: "set by you",
    basedOnObservations: "based on vehicles you looked at",
    needMore: "At least {n} vehicles are needed before a budget can be estimated. Analyse a few in the range you have in mind, or set the range yourself.",
    setRange: "Set the range yourself",
    from: "from",
    to: "to",
    apply: "Apply",
    reset: "Back to the estimate",
    signInOrSet: "Sign in, or set a range yourself.",
    noMatches: "Nothing in this range.",
    atOrBelow: "below its own market",
  },
  disclaimer:
    "AutoIntel is a market-intelligence tool. It does not guarantee the mechanical " +
    "condition, accident history, legal status or future reliability of any vehicle, " +
    "and it is not a substitute for an independent inspection.",
};

export const DICTIONARIES: Record<Locale, Dictionary> = { az, ru, en };
