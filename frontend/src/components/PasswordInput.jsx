// Password field with a show/hide toggle. Forwards the common input props so it
// drops into the existing auth-form <label> markup.
import { useState } from "react";

export default function PasswordInput({
  value,
  onChange,
  autoFocus = false,
  autoComplete = "current-password",
  placeholder,
  minLength,
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="password-field">
      <input
        type={show ? "text" : "password"}
        value={value}
        onChange={onChange}
        required
        autoFocus={autoFocus}
        autoComplete={autoComplete}
        placeholder={placeholder}
        minLength={minLength}
      />
      <button
        type="button"
        className="password-toggle"
        onClick={() => setShow((s) => !s)}
        aria-label={show ? "Hide password" : "Show password"}
        tabIndex={-1}
      >
        {show ? "Hide" : "Show"}
      </button>
    </div>
  );
}
