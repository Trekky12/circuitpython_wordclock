'strict'

const sendColorButton = document.querySelector('button.sendColor');

if(sendColorButton){
  
  const colorInput = document.querySelector('input[name="color"]');
  
  sendColorButton.addEventListener('click', async function (e) {
      e.preventDefault();
      console.log("pressed");
      console.log(colorInput.value);

      let color = colorInput.value;

      const r = parseInt(color.substr(1, 2), 16)
      const g = parseInt(color.substr(3, 2), 16)
      const b = parseInt(color.substr(5, 2), 16)
      console.log(`red: ${r}, green: ${g}, blue: ${b}`);

      await makeRequest("controlColor", {"r": r, "g": g, "b": b})
  });
}

const sendTimezoneButton = document.querySelector('button.sendTimezone');

if(sendTimezoneButton){
  sendTimezoneButton.addEventListener('click', async function (e) {
      e.preventDefault();
      let tz = document.querySelector('select[name="timezone"]').value;
      let auto_dst = document.querySelector('input[name="auto_dst"]').checked;
      await makeRequest("setTimeZone", {"tz": tz, "auto_dst": auto_dst});
  });
}

const sendBrightnessButton = document.querySelector('button.sendBrightness');

if(sendBrightnessButton){
  sendBrightnessButton.addEventListener('click', async function (e) {
      e.preventDefault();
      let auto_brightness = document.querySelector('input[name="auto_brightness"]').checked;
      let brightness = document.querySelector('input[name="brightness"]').value;
      await makeRequest("setBrightness", {"auto_brightness": auto_brightness, "brightness": brightness});
  });
}

const connectButton = document.querySelector('button.connect');

if(connectButton){
  connectButton.addEventListener('click', async function (e) {
      e.preventDefault();
      console.log("pressed");

      let ssid = document.querySelector('select[name="ssid"]').value;
      let password = document.querySelector('input[name="password"]').value;
      makeRequest("connect", {"ssid": ssid, "password": password});

      window.alert("Restarting to connect");
  });
}

async function makeRequest(functionName, data) {
    const response = await fetch("/" + functionName, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(data),
    });
    const result = await response.json();
    console.log(result);
    window.alert(result["msg"]);
}
