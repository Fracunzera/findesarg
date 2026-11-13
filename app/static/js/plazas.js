// Mapa de plazas. La lista (HTML del servidor) es la fuente de verdad:
// los marcadores se arman leyendo los data-* de cada <li class="lugar">.
(function () {
  "use strict";

  const elMapa = document.getElementById("mapa");
  if (!elMapa || !window.L) return;

  const mapa = L.map(elMapa, { scrollWheelZoom: false }).setView(
    [parseFloat(elMapa.dataset.lat), parseFloat(elMapa.dataset.lon)], 13
  );
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">colaboradores de OpenStreetMap</a>',
  }).addTo(mapa);

  const capa = L.layerGroup().addTo(mapa);
  const marcadores = new Map();

  function escapar(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function svg(id) {
    return `<svg class="ico" aria-hidden="true" focusable="false"><use href="#${id}"/></svg>`;
  }

  function seleccionar(id, centrar) {
    document.querySelectorAll(".lugar.activo").forEach((el) => el.classList.remove("activo"));
    document.querySelectorAll(".marcador-mapa--activo").forEach((el) => el.classList.remove("marcador-mapa--activo"));
    const li = document.getElementById("lugar-" + id);
    if (li) {
      const details = li.closest("details");
      if (details) details.open = true;
      li.classList.add("activo");
    }
    const m = marcadores.get(id);
    if (m && m.getElement()) m.getElement().classList.add("marcador-mapa--activo");
    if (m && centrar) {
      mapa.setView(m.getLatLng(), Math.max(mapa.getZoom(), 16));
      m.openPopup();
    }
  }

  function dibujar() {
    capa.clearLayers();
    marcadores.clear();
    const items = document.querySelectorAll("#lista-lugares .lugar");
    const puntos = [];
    items.forEach((li) => {
      const d = li.dataset;
      const lat = parseFloat(d.lat), lon = parseFloat(d.lon);
      const icono = L.divIcon({
        className: "marcador-mapa",
        html: `<span class="sello sello--${d.tipo}">${svg(d.icono)}</span>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15],
        popupAnchor: [0, -14],
      });
      const m = L.marker([lat, lon], {
        icon: icono,
        title: `${d.nombre} (${d.tipoTexto})`, // tooltip y nombre accesible
        keyboard: true,
        riseOnHover: true,
      });
      m.bindPopup(
        `<div class="popup"><p class="popup__nombre">${escapar(d.nombre)}</p>` +
        `<p class="popup__fila"><span class="sello sello--${d.tipo}">${svg(d.icono)}</span> ${escapar(d.tipoTexto)}</p>` +
        `<p class="popup__fila">${svg("i-reloj")} ${escapar(d.horario)}</p>` +
        `<p class="popup__links"><a href="#lugar-${d.id}">Ver en la lista</a>` +
        `<a href="https://www.openstreetmap.org/directions?to=${lat}%2C${lon}" rel="noopener">Cómo llegar →</a></p></div>`
      );
      m.on("click", () => seleccionar(d.id, false));
      m.addTo(capa);
      marcadores.set(d.id, m);
      puntos.push([lat, lon]);
    });
    if (puntos.length) mapa.fitBounds(puntos, { padding: [24, 24], maxZoom: 16 });
  }

  // "Ver en el mapa" desde la lista (delegado: la lista se reemplaza con HTMX)
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".ver-en-mapa");
    if (!btn) return;
    seleccionar(btn.dataset.id, true);
    elMapa.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  document.body.addEventListener("htmx:afterSwap", (ev) => {
    if (ev.detail.target && ev.detail.target.id === "lista-lugares") dibujar();
  });
  document.body.addEventListener("htmx:historyRestore", dibujar);

  // Ordenar por cercanía con la ubicación del navegador (no se guarda en ningún lado)
  const btnCerca = document.getElementById("cercania");
  const estado = document.getElementById("estado-ubicacion");
  let marcadorYo = null;
  if (btnCerca) {
    if (!("geolocation" in navigator)) {
      btnCerca.hidden = true;
    } else {
      btnCerca.addEventListener("click", () => {
        btnCerca.setAttribute("aria-busy", "true");
        estado.textContent = "Buscando tu ubicación…";
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            btnCerca.removeAttribute("aria-busy");
            const { latitude, longitude } = pos.coords;
            document.getElementById("lat").value = latitude.toFixed(5);
            document.getElementById("lon").value = longitude.toFixed(5);
            estado.textContent = "Listo: la lista está ordenada por cercanía.";
            if (marcadorYo) marcadorYo.remove();
            marcadorYo = L.circleMarker([latitude, longitude], {
              radius: 9, weight: 4, color: "#1b1e1f", fillColor: "#ffffff", fillOpacity: 1,
            })
              .bindTooltip("Vos estás acá", { permanent: false }).addTo(mapa);
            htmx.trigger(btnCerca.form, "submit");
          },
          (err) => {
            btnCerca.removeAttribute("aria-busy");
            estado.textContent = "No pudimos obtener tu ubicación" +
              (err.code === 1 ? " (no diste permiso)." : ".");
          },
          { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
        );
      });
    }
  }

  dibujar();
})();
