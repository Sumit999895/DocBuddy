// ==========================================
// FILE INPUT
// ==========================================

const fileInput = document.getElementById("file-input");

const cameraInput = document.getElementById("camera-input");

// ==========================================
// PREVIEW ELEMENTS
// ==========================================

const previewSection = document.getElementById("preview-section");

const previewGrid = document.getElementById("preview-grid");

// ==========================================
// STORE SELECTED IMAGES
// ==========================================

let selectedFiles = [];

// ==========================================
// NORMAL IMAGE UPLOAD
// ==========================================

fileInput.addEventListener("change", function () {
  const files = Array.from(this.files);

  addFiles(files);
});

// ==========================================
// CAMERA IMAGE
// ==========================================

cameraInput.addEventListener("change", function () {
  const files = Array.from(this.files);

  addFiles(files);
});

// ==========================================
// ADD FILES
// ==========================================

function addFiles(files) {
  selectedFiles.push(...files);

  displayPreviews();
}

// ==========================================
// DISPLAY IMAGE PREVIEWS
// ==========================================

function displayPreviews() {
  previewGrid.innerHTML = "";

  previewSection.hidden = selectedFiles.length === 0;

  selectedFiles.forEach((file, index) => {
    const card = document.createElement("div");

    card.className = "preview-card";

    const image = document.createElement("img");

    const imageURL = URL.createObjectURL(file);

    image.src = imageURL;

    const deleteButton = document.createElement("button");

    deleteButton.textContent = "Remove";

    deleteButton.addEventListener("click", () => {
      removeFile(index);
    });

    card.appendChild(image);

    card.appendChild(deleteButton);

    previewGrid.appendChild(card);
  });
}

// ==========================================
// REMOVE IMAGE
// ==========================================

function removeFile(index) {
  selectedFiles.splice(index, 1);

  displayPreviews();
}
