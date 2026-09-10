document.addEventListener("DOMContentLoaded", function () {
  /* ======================================================
     FILE NAME DISPLAY
  ====================================================== */

  const fileInput = document.getElementById("document-file");

  const fileName = document.getElementById("file-name");

  if (fileInput && fileName) {
    fileInput.addEventListener("change", function () {
      if (fileInput.files && fileInput.files.length > 0) {
        fileName.textContent = fileInput.files[0].name;
      } else {
        fileName.textContent = "No file selected";
      }
    });
  }

  /* ======================================================
     FLASH MESSAGE
  ====================================================== */

  const alerts = document.querySelectorAll(".alert");

  alerts.forEach(function (alert) {
    setTimeout(function () {
      alert.style.opacity = "0";

      alert.style.transform = "translateY(-10px)";

      setTimeout(function () {
        alert.remove();
      }, 300);
    }, 3500);
  });
});
// ========================================
// AUTO HIDE FLASH MESSAGES
// ========================================

document.addEventListener("DOMContentLoaded", () => {
  const alerts = document.querySelectorAll("#flash-container .alert");

  alerts.forEach((alert) => {
    setTimeout(() => {
      alert.classList.add("hide");

      setTimeout(() => {
        alert.remove();
      }, 400);
    }, 3000);
  });
});
