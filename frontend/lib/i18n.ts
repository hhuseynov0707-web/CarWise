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
  disclaimer:
    "AutoIntel is a market-intelligence tool. It does not guarantee the mechanical " +
    "condition, accident history, legal status or future reliability of any vehicle, " +
    "and it is not a substitute for an independent inspection.",
};

export const DICTIONARIES: Record<Locale, Dictionary> = { az, ru, en };
