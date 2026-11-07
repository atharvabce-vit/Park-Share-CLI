(function () {
  "use strict";

  var defaultCenter = [19.076, 72.8777];
  var geocodeButtons;

  function statusFor(mapId) {
    return document.querySelector('[data-map-status="' + mapId + '"]');
  }

  function setStatus(mapId, message) {
    var status = statusFor(mapId);
    if (status) {
      status.textContent = message;
    }
  }

  function coordinatesFromInputs(mapElement) {
    var latitudeInput = document.getElementById(mapElement.dataset.latitudeInput);
    var longitudeInput = document.getElementById(mapElement.dataset.longitudeInput);
    if (!latitudeInput || !longitudeInput || !latitudeInput.value || !longitudeInput.value) {
      return null;
    }
    var latitude = Number(latitudeInput.value);
    var longitude = Number(longitudeInput.value);
    return Number.isFinite(latitude) && Number.isFinite(longitude) ?
      [latitude, longitude] : null;
  }

  function writeCoordinates(mapElement, marker, latitude, longitude) {
    var latitudeInput = document.getElementById(mapElement.dataset.latitudeInput);
    var longitudeInput = document.getElementById(mapElement.dataset.longitudeInput);
    if (!latitudeInput || !longitudeInput) {
      return;
    }
    latitudeInput.value = latitude.toFixed(6);
    longitudeInput.value = longitude.toFixed(6);
    marker.setLatLng([latitude, longitude]);
    marker.addTo(mapElement._parkingLeafletMap);
    mapElement._parkingLeafletMap.setView([latitude, longitude], 16);
    if (mapElement.dataset.mapMode === "search") {
      setStatus(mapElement.id, "Searching within " +
        document.getElementById(mapElement.dataset.radiusInput).value + " km of this pin.");
    } else {
      setStatus(mapElement.id, "Parking entrance pinned at " + latitude.toFixed(5) +
        ", " + longitude.toFixed(5) + ".");
    }
  }

  function initializeMap(mapElement) {
    if (!window.L) {
      setStatus(mapElement.id, "Map library could not load. Check your internet connection and reload.");
      return;
    }

    var selectedCoordinates = coordinatesFromInputs(mapElement);
    var map = window.L.map(mapElement, { scrollWheelZoom: false }).setView(
      selectedCoordinates || defaultCenter, selectedCoordinates ? 15 : 12
    );
    mapElement._parkingLeafletMap = map;
    window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    var pin = window.L.marker(selectedCoordinates || defaultCenter, { draggable: true });
    mapElement._parkingPin = pin;
    if (selectedCoordinates) {
      pin.addTo(map);
    }
    pin.on("dragend", function (event) {
      var position = event.target.getLatLng();
      writeCoordinates(mapElement, pin, position.lat, position.lng);
    });

    if (mapElement.dataset.mapMode === "picker") {
      map.on("click", function (event) {
        writeCoordinates(mapElement, pin, event.latlng.lat, event.latlng.lng);
        var locationInput = document.getElementById(mapElement.dataset.locationInput);
        if (locationInput && !locationInput.value.trim()) {
          locationInput.value = "Map pin (" + event.latlng.lat.toFixed(5) +
            ", " + event.latlng.lng.toFixed(5) + ")";
        }
      });
    } else {
      map.on("click", function (event) {
        writeCoordinates(mapElement, pin, event.latlng.lat, event.latlng.lng);
      });
      var radiusInput = document.getElementById(mapElement.dataset.radiusInput);
      if (radiusInput) {
        radiusInput.addEventListener("change", function () {
          if (selectedCoordinates || coordinatesFromInputs(mapElement)) {
            setStatus(mapElement.id, "Search radius set to " + radiusInput.value + " km.");
          }
        });
      }
      try {
        var markers = JSON.parse(mapElement.dataset.markers || "[]");
        var markerLocations = [];
        markers.forEach(function (item) {
          if (!Number.isFinite(item.latitude) || !Number.isFinite(item.longitude)) {
            return;
          }
          var marker = window.L.marker([item.latitude, item.longitude]).addTo(map);
          markerLocations.push([item.latitude, item.longitude]);
          var popup = document.createElement("div");
          var label = document.createElement("strong");
          label.textContent = item.label;
          var details = document.createElement("p");
          details.textContent = "₹" + item.price + " / hour · Fits " +
            item.vehicle_types.join(", ");
          popup.appendChild(label);
          popup.appendChild(details);
          marker.bindPopup(popup);
        });
        if (!selectedCoordinates && markerLocations.length) {
          map.fitBounds(markerLocations, { padding: [20, 20], maxZoom: 13 });
        }
      } catch (error) {
        setStatus(mapElement.id, "Could not load listing markers.");
      }
    }

    if (selectedCoordinates) {
      setStatus(mapElement.id, "Map centered on the selected pin.");
    }
  }

  function geocode(button) {
    var input = document.getElementById(button.dataset.mapGeocode);
    var mapElement = document.getElementById(button.dataset.mapTarget);
    if (!input || !mapElement || !mapElement._parkingLeafletMap) {
      return;
    }
    var query = input.value.trim();
    if (!query) {
      setStatus(mapElement.id, "Enter a place name first, or click the map to choose a point.");
      return;
    }
    button.disabled = true;
    setStatus(mapElement.id, "Finding that place...");
    fetch("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=" +
      encodeURIComponent(query), { headers: { "Accept": "application/json" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Place search returned HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (places) {
        if (!places.length) {
          setStatus(mapElement.id, "No matching place found. Try a nearby street or area.");
          return;
        }
        var latitude = Number(places[0].lat);
        var longitude = Number(places[0].lon);
        var pin = mapElement._parkingPin;
        writeCoordinates(mapElement, pin, latitude, longitude);
        if (mapElement.dataset.mapMode === "picker") {
          var locationInput = document.getElementById(mapElement.dataset.locationInput);
          if (locationInput && !locationInput.value.trim()) {
            locationInput.value = places[0].display_name.slice(0, 120);
          }
          setStatus(mapElement.id, "Found " + places[0].display_name +
            ". Adjust the pin to the exact parking entrance.");
        } else {
          setStatus(mapElement.id, "Found " + places[0].display_name +
            ". Choose a radius, then search this area.");
        }
      })
      .catch(function () {
        setStatus(mapElement.id, "Place search is temporarily unavailable. Click the map to choose the point.");
      })
      .finally(function () {
        button.disabled = false;
      });
  }

  function useBrowserLocation(button) {
    var mapElement = document.getElementById(button.dataset.mapTarget);
    if (!mapElement) {
      return;
    }
    if (!navigator.geolocation) {
      setStatus(mapElement.id, "Location access is not available in this browser.");
      return;
    }
    button.disabled = true;
    setStatus(mapElement.id, "Waiting for location permission...");
    navigator.geolocation.getCurrentPosition(function (position) {
      writeCoordinates(
        mapElement, mapElement._parkingPin,
        position.coords.latitude, position.coords.longitude
      );
      button.disabled = false;
    }, function () {
      setStatus(mapElement.id, "Could not read your location. You can still click the map.");
      button.disabled = false;
    }, { enableHighAccuracy: true, timeout: 10000 });
  }

  document.querySelectorAll("[data-parking-map]").forEach(function (mapElement) {
    initializeMap(mapElement);
    var details = mapElement.closest("details");
    if (details) {
      details.addEventListener("toggle", function () {
        if (details.open && mapElement._parkingLeafletMap) {
          window.setTimeout(function () { mapElement._parkingLeafletMap.invalidateSize(); }, 100);
        }
      });
    }
  });

  geocodeButtons = document.querySelectorAll("[data-map-geocode]");
  geocodeButtons.forEach(function (button) {
    button.addEventListener("click", function () { geocode(button); });
  });

  document.querySelectorAll("[data-map-geolocate]").forEach(function (button) {
    button.addEventListener("click", function () { useBrowserLocation(button); });
  });
}());
