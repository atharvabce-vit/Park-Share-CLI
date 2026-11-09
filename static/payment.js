(function () {
  document.querySelectorAll(".checkout-form").forEach(function (form) {
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      var button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      try {
        var response = await fetch(form.action, {
          method: "POST",
          body: new URLSearchParams(new FormData(form)),
          headers: { "Accept": "application/json" }
        });
        var order = await response.json();
        if (!response.ok) {
          throw new Error(order.error || "Could not start payment. Please try again.");
        }
        var checkout = new Razorpay({
          key: order.key_id,
          amount: order.amount,
          currency: order.currency,
          name: "ParkShare",
          description: "Parking booking checkout",
          order_id: order.order_id,
          handler: async function (payment) {
            try {
              var verification = new URLSearchParams(new FormData(form));
              verification.set("razorpay_order_id", payment.razorpay_order_id);
              verification.set("razorpay_payment_id", payment.razorpay_payment_id);
              verification.set("razorpay_signature", payment.razorpay_signature);
              var result = await fetch(order.verify_url, {
                method: "POST",
                body: verification,
                headers: { "Accept": "application/json" }
              });
              if (!result.ok) {
                throw new Error("Payment could not be verified yet. Keep this page open and retry checkout.");
              }
              window.location.assign(result.url || window.location.href);
            } catch (error) {
              button.disabled = false;
              window.alert(error.message);
            }
          },
          modal: {
            ondismiss: function () {
              button.disabled = false;
            }
          }
        });
        checkout.on("payment.failed", function () {
          button.disabled = false;
          window.alert("Payment did not complete. You can try checkout again.");
        });
        checkout.open();
      } catch (error) {
        button.disabled = false;
        window.alert(error.message);
      }
    });
  });
}());
