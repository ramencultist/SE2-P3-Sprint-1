/*
 * Module Name: MainActivity
 * Description: Provides Traffic, Police, and Hazard selections and schedules a dry-run MacroDroid broadcast without subcategories.
 * 
 * Inputs: Selected report category, Send and Cancel taps, Android lifecycle events, and system time.
 * Outputs: String extras sent with com.wazeomatic.HAZARD_DETECTED, countdown and status text, toast messages, and sender logs.
 * External Sources: ChatGPT 6.1 Sol
 * Attributions: Android SDK and Gradle build tooling
 * 
 * Creation 11/10/2026 - Drew and ChatGPT:
 * A description of how and why AI was used: AI assisted with creating the tester to category-only Traffic, Police, and Hazard reports, and updating the setup documentation.
 * 
 * How you validated and revised the AI output: Built the APK, installed it on a device. Used the different categories of reports and verified that it worked with the macro on Android.
 * 
 * The challenges or limitations you faced while using AI: Aligning the intent of AI generated content with the purpose of the project and desired categories of Police, Traffic, and Hazard. Attempted to generate other categories/subcategories.
 * Authors: Drew Medlock
 * Creation Date: 11/10/2026
 */

package com.wazeomatic.intenttester;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

/** Manual sender for the same string extras used by the detector. */
public class MainActivity extends Activity {
    private static final String ACTION = "com.wazeomatic.HAZARD_DETECTED";
    private static final String[] TYPES = {
        "TRAFFIC", "POLICE", "HAZARD"
    };
    private static final String[] CATEGORIES = {"Traffic", "Police", "Hazard"};
    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView status;
    private Spinner report;
    private Button send;
    private Button cancel;
    private int pendingReport;
    private long deadline;

    private final Runnable countdown = new Runnable() {
        @Override public void run() {
            long remaining = deadline - SystemClock.elapsedRealtime();
            if (remaining > 0) {
                status.setText("Sending " + CATEGORIES[pendingReport] + " in "
                    + ((remaining + 999) / 1000) + " seconds. Switch to Waze now.");
                handler.postDelayed(this, Math.min(1000, remaining));
            } else {
                broadcast(pendingReport);
            }
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding, padding, padding);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(layout);
        setContentView(scroll);
        // Keep controls clear of system bars on Android 15's edge-to-edge layout.
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        scroll.requestApplyInsets();

        TextView title = text(layout, "MacroDroid Intent Tester");
        title.setTextSize(26);
        text(layout, "Select Traffic, Police, or Hazard (no subcategories), then schedule a test "
            + "and switch to Waze within 10 seconds. "
            + "Keep MacroDroid enabled and its global DRY_RUN variable true. "
            + "The macro must cancel before Waze's auto-send countdown finishes.");
        text(layout, "Every broadcast uses dry_run=\"true\". The receiving macro must honor it.");
        report = new Spinner(this);
        report.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item,
            CATEGORIES));
        layout.addView(report);
        send = new Button(this);
        send.setText("Send dry-run intent in 10 seconds");
        layout.addView(send);
        cancel = new Button(this);
        cancel.setText("Cancel pending test");
        cancel.setEnabled(false);
        layout.addView(cancel);
        status = text(layout, "Ready. Allow 60 seconds between tests if your macro has a cooldown.");
        text(layout, "Action: " + ACTION + "\nExtras are strings: hazard_type, confidence=0.900, "
            + "source=sim, ts, waze_category=selected category, waze_subcategory=\"\", dry_run=true. "
            + "MacroDroid must map TRAFFIC, POLICE, and HAZARD to their category taps only. "
            + "No detector, camera, network, or special permissions needed.");
        send.setOnClickListener(view -> {
            pendingReport = report.getSelectedItemPosition();
            deadline = SystemClock.elapsedRealtime() + 10_000;
            setPending(true);
            handler.post(countdown);
        });
        cancel.setOnClickListener(view -> {
            handler.removeCallbacks(countdown);
            setPending(false);
            status.setText("Test cancelled. No intent sent.");
        });
        if (state != null) status.setText("Ready. Any pending test was cancelled when the activity was recreated.");
    }

    private TextView text(LinearLayout layout, String value) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(16);
        view.setPadding(0, 12, 0, 12);
        layout.addView(view);
        return view;
    }

    private void setPending(boolean pending) {
        send.setEnabled(!pending);
        report.setEnabled(!pending);
        cancel.setEnabled(pending);
    }

    private void broadcast(int index) {
        Intent intent = new Intent(ACTION);
        intent.putExtra("hazard_type", TYPES[index]);
        intent.putExtra("confidence", "0.900");
        intent.putExtra("source", "sim");
        intent.putExtra("ts", java.lang.String.format(java.util.Locale.US, "%.3f", System.currentTimeMillis() / 1000.0));
        intent.putExtra("waze_category", CATEGORIES[index]);
        intent.putExtra("waze_subcategory", "");
        intent.putExtra("dry_run", "true");
        sendBroadcast(intent);
        String message = "Broadcast sent: " + TYPES[index] + " (dry_run=true). "
            + "Delivery and macro execution are not confirmed by this app.";
        Log.i("WazeomaticIntentTester", message);
        status.setText(message);
        Toast.makeText(this, "Dry-run " + CATEGORIES[index] + " broadcast sent", Toast.LENGTH_SHORT).show();
        setPending(false);
    }

    @Override protected void onDestroy() {
        handler.removeCallbacks(countdown);
        super.onDestroy();
    }
}
