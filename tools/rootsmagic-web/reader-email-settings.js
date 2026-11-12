const encryption = document.querySelector("#smtp-security");
const port = document.querySelector("#smtp-port");

if (encryption instanceof HTMLSelectElement && port instanceof HTMLInputElement) {
  encryption.addEventListener("change", () => {
    port.value = encryption.selectedOptions[0].dataset.port;
  });
}
