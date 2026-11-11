/*
 * Module Name: Root Gradle build
 * Description: Declares the Android application plugin version for the standalone project.
 * 
 * Inputs: Gradle plugin repositories and the Android Gradle plugin version.
 * Outputs: Android plugin availability for the app module.
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
    id("com.android.application") version "8.7.3" apply false
}
