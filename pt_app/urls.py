from django.urls import path
from . import views
from .views import WorkoutList, ExerciseList, ExerciseCreate, Exercises

urlpatterns = [
    path('test-api/', views.test_api, name='test_api'),
    path('workouts/', WorkoutList.as_view(), name='workout_list'),
    path('exercises/<int:id>', ExerciseList.as_view(), name='exercise_list'),
    path('exercises/', Exercises.as_view(), name='exercises'),
    path('exercise_create/', ExerciseCreate.as_view(), name='exercise_create')
]