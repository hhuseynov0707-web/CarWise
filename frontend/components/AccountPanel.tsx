"use client";

import { useState } from "react";
import { Callout, Card, Spinner } from "@/components/ui";
import { ApiError, register, signIn, updateProfile } from "@/lib/api";
import { LOCALES, LOCALE_NAMES, type Locale } from "@/lib/i18n";
import { useLocale } from "@/lib/locale";
import { useSession } from "@/lib/session";

/**
 * The Profile tab: sign in, register, or the signed-in profile.
 *
 * One component because they are one screen from the visitor's side — which
 * of the three appears is a fact about them, not a route they chose.
 */
export function AccountPanel() {
  const { user, loading } = useSession();

  if (loading) {
    return (
      <Card>
        <Spinner label="" />
      </Card>
    );
  }
  return user ? <Profile /> : <SignInOrRegister />;
}

function SignInOrRegister() {
  const { t, locale } = useLocale();
  const { setUser } = useSession();
  const [mode, setMode] = useState<"signIn" | "register">("signIn");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [birthYear, setBirthYear] = useState("");

  const registering = mode === "register";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = registering
        ? await register({
            email,
            password,
            first_name: firstName || null,
            last_name: lastName || null,
            birth_year: birthYear ? Number(birthYear) : null,
            locale,
          })
        : await signIn(email, password);
      setUser(user);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-md">
      <h1 className="text-figure-lg font-semibold tracking-tight text-ink">
        {registering ? t.auth.registerTitle : t.auth.signInTitle}
      </h1>
      <p className="mt-2 text-sm text-ink-soft">
        {registering ? t.auth.registerLead : t.auth.signInLead}
      </p>

      <form onSubmit={submit} className="card mt-6 grid gap-4">
        <Field
          id="email"
          label={t.auth.email}
          type="email"
          value={email}
          onChange={setEmail}
          autoComplete="email"
          required
        />
        <Field
          id="password"
          label={t.auth.password}
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete={registering ? "new-password" : "current-password"}
          hint={registering ? t.auth.passwordHint : undefined}
          minLength={registering ? 10 : undefined}
          required
        />

        {registering ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                id="first-name"
                label={`${t.auth.firstName} (${t.auth.optional})`}
                value={firstName}
                onChange={setFirstName}
                autoComplete="given-name"
              />
              <Field
                id="last-name"
                label={`${t.auth.lastName} (${t.auth.optional})`}
                value={lastName}
                onChange={setLastName}
                autoComplete="family-name"
              />
            </div>
            <Field
              id="birth-year"
              label={`${t.auth.birthYear} (${t.auth.optional})`}
              value={birthYear}
              onChange={(v) => setBirthYear(v.replace(/\D/g, "").slice(0, 4))}
              inputMode="numeric"
              hint={t.auth.birthYearHint}
            />
          </>
        ) : null}

        {error ? (
          <Callout tone="warning" title={error}>
            {null}
          </Callout>
        ) : null}

        <button
          type="submit"
          disabled={busy}
          className="min-h-[44px] cursor-pointer rounded-md bg-accent px-4 text-sm font-medium
                     text-white transition-colors duration-200 hover:bg-accent/90
                     disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? t.auth.working : registering ? t.auth.submitRegister : t.auth.submitSignIn}
        </button>
      </form>

      <button
        type="button"
        onClick={() => {
          setMode(registering ? "signIn" : "register");
          setError(null);
        }}
        className="mt-4 min-h-[44px] cursor-pointer text-sm text-accent hover:underline"
      >
        {registering ? t.auth.toSignIn : t.auth.toRegister}
      </button>
    </div>
  );
}

function Profile() {
  const { t } = useLocale();
  const { user, setUser, signOut } = useSession();
  const { setLocale } = useLocale();

  const [firstName, setFirstName] = useState(user?.first_name ?? "");
  const [lastName, setLastName] = useState(user?.last_name ?? "");
  const [birthYear, setBirthYear] = useState(user?.birth_year?.toString() ?? "");
  const [chosenLocale, setChosenLocale] = useState<Locale>((user?.locale as Locale) ?? "az");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      const updated = await updateProfile({
        first_name: firstName,
        last_name: lastName,
        birth_year: birthYear ? Number(birthYear) : null,
        locale: chosenLocale,
      });
      setUser(updated);
      setLocale(chosenLocale);
      setDone(true);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-md">
      <h1 className="text-figure-lg font-semibold tracking-tight text-ink">{t.profile.title}</h1>
      <p className="mt-2 text-sm text-ink-soft">
        {t.profile.signedInAs} <span className="font-medium text-ink">{user?.email}</span>
      </p>

      <form onSubmit={save} className="card mt-6 grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="p-first" label={t.auth.firstName} value={firstName} onChange={setFirstName} />
          <Field id="p-last" label={t.auth.lastName} value={lastName} onChange={setLastName} />
        </div>
        <Field
          id="p-year"
          label={t.auth.birthYear}
          value={birthYear}
          onChange={(v) => setBirthYear(v.replace(/\D/g, "").slice(0, 4))}
          inputMode="numeric"
          hint={t.auth.birthYearHint}
        />

        <div>
          <label className="label" htmlFor="p-locale">
            {t.actions.language}
          </label>
          <select
            id="p-locale"
            className="field"
            value={chosenLocale}
            onChange={(event) => setChosenLocale(event.target.value as Locale)}
          >
            {LOCALES.map((code) => (
              <option key={code} value={code}>
                {LOCALE_NAMES[code]}
              </option>
            ))}
          </select>
        </div>

        {error ? (
          <Callout tone="warning" title={error}>
            {null}
          </Callout>
        ) : null}

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={busy}
            className="min-h-[44px] cursor-pointer rounded-md bg-accent px-4 text-sm font-medium
                       text-white transition-colors duration-200 hover:bg-accent/90
                       disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? t.auth.working : t.profile.save}
          </button>
          {done ? <span className="text-sm text-rating-great">{t.profile.saved}</span> : null}
        </div>
      </form>

      <p className="mt-4 text-xs leading-relaxed text-ink-muted">{t.profile.dataNote}</p>

      <button
        type="button"
        onClick={() => void signOut()}
        className="mt-6 min-h-[44px] cursor-pointer rounded-md border border-surface-border px-4
                   text-sm text-ink-soft transition-colors duration-200 hover:bg-surface"
      >
        {t.profile.signOut}
      </button>
    </div>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  hint,
  type = "text",
  ...rest
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  type?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange" | "value" | "type" | "id">) {
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type={type}
        className="field"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={hint ? `${id}-hint` : undefined}
        {...rest}
      />
      {hint ? (
        <p id={`${id}-hint`} className="mt-1 text-xs text-ink-faint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
