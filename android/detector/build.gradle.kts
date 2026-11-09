/*
Module Name: build.gradle.kts
Description: Top-level Gradle build script for the detector app; declares the Android and Kotlin plugin versions

Inputs: None
Outputs: Plugin versions for the app module
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to set up the Gradle project for the detector app
How you validated and revised the AI output: Proofreading, and building the app with gradlew assembleDebug
The challenges or limitations you faced while using AI: Choosing plugin versions compatible with the installed SDK
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
}
