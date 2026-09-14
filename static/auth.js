(function () {
  "use strict";

  // ------------------------------------------------------------
  // Show/hide password
  // ------------------------------------------------------------
  document.querySelectorAll(".password-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.getElementById(btn.dataset.toggleFor);
      if (!input) return;
      var showing = input.type === "text";
      input.type = showing ? "password" : "text";
      btn.setAttribute(
        "aria-label",
        showing ? "Show password" : "Hide password",
      );
      btn.style.color = showing ? "" : "#4f46e5";
    });
  });

  // ------------------------------------------------------------
  // Live email validation (basic client-side check; the server
  // always re-validates properly, including domain deliverability)
  // ------------------------------------------------------------
  var emailInput = document.getElementById("email");
  var emailError = document.getElementById("email-error");
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  if (emailInput && emailError) {
    emailInput.addEventListener("blur", function () {
      if (!emailInput.value) return;
      var valid = EMAIL_RE.test(emailInput.value);
      emailInput.classList.toggle("is-invalid", !valid);
      emailInput.classList.toggle("is-valid", valid);
      emailError.textContent = valid ? "" : "Enter a valid email address.";
    });
  }

  // ------------------------------------------------------------
  // Password strength meter
  // ------------------------------------------------------------
  var pwInput = document.getElementById("password");
  var strengthFill = document.getElementById("strength-fill");

  if (pwInput && strengthFill) {
    pwInput.addEventListener("input", function () {
      var v = pwInput.value;
      var score = 0;
      if (v.length >= 8) score++;
      if (/[A-Z]/.test(v)) score++;
      if (/[0-9]/.test(v)) score++;
      if (/[^A-Za-z0-9]/.test(v)) score++;

      var pct = (score / 4) * 100;
      var color = "#dc2626";
      if (score >= 3) color = "#16a34a";
      else if (score === 2) color = "#f59e0b";

      strengthFill.style.width = pct + "%";
      strengthFill.style.backgroundColor = color;
    });
  }

  // ------------------------------------------------------------
  // Confirm-password match check
  // ------------------------------------------------------------
  var confirmInput = document.getElementById("confirm_password");
  var confirmError = document.getElementById("confirm-error");

  if (pwInput && confirmInput && confirmError) {
    var checkMatch = function () {
      if (!confirmInput.value) return;
      var match = confirmInput.value === pwInput.value;
      confirmInput.classList.toggle("is-invalid", !match);
      confirmInput.classList.toggle("is-valid", match);
      confirmError.textContent = match ? "" : "Passwords do not match.";
    };
    confirmInput.addEventListener("input", checkMatch);
    pwInput.addEventListener("input", checkMatch);
  }

  // ------------------------------------------------------------
  // OTP boxes: auto-advance, backspace-back, and paste-to-fill
  // ------------------------------------------------------------
  var otpBoxes = Array.prototype.slice.call(
    document.querySelectorAll(".otp-box"),
  );

  if (otpBoxes.length) {
    otpBoxes[0].focus();

    otpBoxes.forEach(function (box, idx) {
      box.addEventListener("input", function () {
        box.value = box.value.replace(/[^0-9]/g, "").slice(0, 1);
        if (box.value && idx < otpBoxes.length - 1) {
          otpBoxes[idx + 1].focus();
        }
      });

      box.addEventListener("keydown", function (e) {
        if (e.key === "Backspace" && !box.value && idx > 0) {
          otpBoxes[idx - 1].focus();
        }
      });

      box.addEventListener("paste", function (e) {
        var pasted = (e.clipboardData || window.clipboardData)
          .getData("text")
          .replace(/[^0-9]/g, "");
        if (!pasted) return;
        e.preventDefault();
        for (var i = 0; i < otpBoxes.length; i++) {
          otpBoxes[i].value = pasted[i] || "";
        }
        var lastFilled = Math.min(pasted.length, otpBoxes.length) - 1;
        if (lastFilled >= 0) otpBoxes[lastFilled].focus();
      });
    });
  }

  // ------------------------------------------------------------
  // Resend-code cooldown timer (purely cosmetic; the server also
  // enforces its own cooldown independently)
  // ------------------------------------------------------------
  var resendBtn = document.getElementById("resend-btn");
  if (resendBtn) {
    var startCooldown = function (seconds) {
      var remaining = seconds;
      resendBtn.disabled = true;
      var original = "Resend code";
      var tick = function () {
        if (remaining <= 0) {
          resendBtn.disabled = false;
          resendBtn.textContent = original;
          return;
        }
        resendBtn.textContent = "Resend code (" + remaining + "s)";
        remaining--;
        setTimeout(tick, 1000);
      };
      tick();
    };
    startCooldown(45);
  }

  // ------------------------------------------------------------
  // Show a spinner + disable the button on submit, so a slow
  // network / SMTP call doesn't invite a double-click.
  // ------------------------------------------------------------
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector(".login-btn");
      if (btn) {
        btn.classList.add("is-loading");
        btn.disabled = true;
      }
    });
  });
})();
