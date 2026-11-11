/*
 * Module Name: Android app build
 * Description: Configures the intent tester application, Android SDK levels, version, and Java compatibility.
 * 
 * Inputs: Android application plugin, JDK 17 or later, Android SDK platform 35, and app source files.
 * Outputs: Android build tasks and the intent tester APK.
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

plugins {
    id("com.android.application")
}

android {
    namespace = "com.wazeomatic.intenttester"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.wazeomatic.intenttester"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
