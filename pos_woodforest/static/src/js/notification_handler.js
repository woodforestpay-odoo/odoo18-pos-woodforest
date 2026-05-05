/** @odoo-module **/

import { registry } from "@web/core/registry";

// Add a service to intercept action execution and notifications
const payrilliumNotificationService = {
  dependencies: ["action", "notification"],
  start(env, { action: actionService, notification: notificationService }) {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return { action: actionService, notification: notificationService };
    }
    const originalDoAction = actionService.doAction.bind(actionService);
    const originalAdd = notificationService.add.bind(notificationService);

    // Intercept notifications to handle payment success messages
    notificationService.add = function (message, options = {}) {
      const result = originalAdd(message, options);

      try {
        // Check if this is a payment-related notification (sticky)
        if (
          options.sticky &&
          typeof message === "string" &&
          (message.includes("Processed") ||
            message.includes("Found and processed") ||
            message.includes("payment link(s) are active") ||
            message.includes("No payments found") ||
            message.includes("No active payment links"))
        ) {
          console.log(
            "Woodforest: Payment notification detected, will close after 5 seconds...",
          );

          // Close the notification after 5 seconds
          setTimeout(() => {
            // Find the notification element and close it
            const notifications = document.querySelectorAll(
              ".o_notification_manager .o_notification",
            );
            notifications.forEach((notification) => {
              if (
                notification.textContent &&
                notification.textContent.includes(message)
              ) {
                const closeButton = notification.querySelector(
                  ".o_notification_close",
                );
                if (closeButton) {
                  closeButton.click();
                }
              }
            });

            // Only reload the page if it's a success notification with actual payments processed
            if (
              message.includes("Processed") &&
              message.includes("/") &&
              !message.includes("0/") &&
              !message.includes("0 payment")
            ) {
              console.log(
                "Woodforest: Payment processed notification detected, reloading page...",
              );
              window.location.reload();
            } else {
              console.log(
                "Payrillium: Closing payment notification (no reload needed)...",
              );
            }
          }, 5000);
        }
      } catch (e) {
        console.error(
          "Payrillium: Error in notification handler side-effect",
          e,
        );
      }

      return result;
    };

    // Intercept action execution for reload actions
    actionService.doAction = async function (actionRequest, options = {}) {
      // Check if this is a reload action
      if (actionRequest && actionRequest.tag === "reload") {
        // Check if there's a payment notification visible
        const recentNotifications = document.querySelectorAll(
          ".o_notification_manager .o_notification",
        );
        let isPaymentReload = false;

        recentNotifications.forEach((notification) => {
          const message = notification.textContent || "";
          if (
            message.includes("Processed") ||
            message.includes("Found and processed")
          ) {
            isPaymentReload = true;
          }
        });

        if (isPaymentReload) {
          // Delay the reload by 5 seconds for payment notifications
          console.log(
            "Payment notification detected, delaying reload by 5 seconds...",
          );
          await new Promise((resolve) => setTimeout(resolve, 5000));
        }
      }

      // Execute the original action
      return originalDoAction(actionRequest, options);
    };

    return { action: actionService, notification: notificationService };
  },
};

registry
  .category("services")
  .add("payrillium_notification_handler", payrilliumNotificationService);
