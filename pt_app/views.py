from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework import viewsets
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.generics import RetrieveAPIView, RetrieveUpdateAPIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from .models import (Program, Workout, Exercise, WorkoutExercise, UserProgramProgress, WorkoutSession, ExerciseLog, ExerciseSet, 
                    User, Message, ChatSession)
from .serializers import (MyTokenObtainPairSerializer, ProgramSerializer, WorkoutSerializer, ExerciseSerializer, WorkoutExerciseSerializer, 
                        WorkoutSessionSerializer, ExerciseSetSerializer, UserSerializer, MessageSerializer, ChatSessionSerializer,
                        ExerciseSetVideoSerializer, ExerciseLogSerializer, WorkoutOrderSerializer, ExerciseOrderSerializer, UserRegistrationSerializer,
                        PublicKeySerializer, TrainerRequestSerializer, GuestRegistrationSerializer)
from .utils import set_or_update_user_program_progress, start_workout_session, get_chat_session, get_messages_for_session
from .models import User, TrainerRequest, TrainerClientRelationship
from rest_framework import permissions, status, views
import openai
import json
from django.conf import settings
from django.utils.timezone import now
from datetime import timedelta, datetime, time
from collections import OrderedDict
from django.db.models import Exists, OuterRef
from rest_framework.decorators import api_view, permission_classes
from django.core.exceptions import ObjectDoesNotExist
from rest_framework_simplejwt.tokens import RefreshToken
from django.http import Http404
from django.utils import timezone

def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }

class UserRegistrationView(APIView):
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            if user:
                tokens = get_tokens_for_user(user)
                return Response({"tokens": tokens, "message": "User created successfully"}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class GuestUserCreateAPIView(APIView):
    def post(self, request, *args, **kwargs):
        # Instantiate your serializer with empty data as your logic generates it
        serializer = GuestRegistrationSerializer(data={})
        if serializer.is_valid():
            user, plain_password = serializer.save()  # Unpack the returned tuple

            # Create token manually using the custom serializer with the plain password
            token_serializer = MyTokenObtainPairSerializer(data={
                'username': user.username,
                'password': plain_password  # Use the plain password here
            })

            # Validate and generate tokens
            if token_serializer.is_valid():
                tokens = token_serializer.validated_data
                return Response({
                    'tokens': tokens,
                    'message': 'Guest user created successfully'
                }, status=status.HTTP_201_CREATED)
            else:
                return Response(token_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class UserDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]  # Ensure only authenticated users can access

    def delete(self, request):
        # Directly use request.user since it's always present for authenticated users
        user = request.user
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
class UpdatePublicKeyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PublicKeySerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            user = request.user
            user.public_key = serializer.validated_data['public_key']
            user.save()
            return Response({"message": "Public key updated successfully"}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer

class SendTrainerRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, user_id):
        from_user = request.user
        to_user = User.objects.get(pk=user_id)
        if from_user == to_user:
            return Response({'error': 'You cannot send a trainer request to yourself'}, status=status.HTTP_400_BAD_REQUEST)

        trainer_request, created = TrainerRequest.objects.get_or_create(from_user=from_user, to_user=to_user, is_active=True)
        if created:
            return Response({
                'id': trainer_request.id,
                'status': 'Trainer request sent',
                'from_user': from_user.id,
                'to_user': to_user.id,
                'created_at': trainer_request.created_at.isoformat(),  # Assumes your model has a 'created_at' field
                'is_active': trainer_request.is_active
            }, status=status.HTTP_201_CREATED)
        else:
            return Response({'error': 'Trainer request already exists'}, status=status.HTTP_400_BAD_REQUEST)
        
class UserTrainerRequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        received_requests = TrainerRequest.objects.filter(to_user=user, is_active=True)
        sent_requests = TrainerRequest.objects.filter(from_user=user, is_active=True)
        data = {
            'received_requests': TrainerRequestSerializer(received_requests, many=True).data,
            'sent_requests': TrainerRequestSerializer(sent_requests, many=True).data,
        }
        return Response(data, status=status.HTTP_200_OK)

class HandleTrainerRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        trainer_request = TrainerRequest.objects.get(pk=request_id)
        if request.user != trainer_request.to_user:
            return Response({'error': 'You do not have permission to handle this request'}, status=status.HTTP_403_FORBIDDEN)

        action = request.data.get('action')

        if action == 'accept':
            TrainerClientRelationship.objects.create(trainer=trainer_request.from_user, client=trainer_request.to_user)
            trainer_request.is_active = False
            trainer_request.save()
            return Response({'status': 'Trainer request accepted'}, status=status.HTTP_200_OK)
        elif action == 'reject':
            trainer_request.is_active = False
            trainer_request.save()
            return Response({'status': 'Trainer request rejected'}, status=status.HTTP_200_OK)
        else:
            return Response({'error': 'Invalid action'}, status=status.HTTP_400_BAD_REQUEST)
        
class RemoveClientView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, client_id):
        # request.user is the trainer, and client_id is from the URL
        relationship = get_object_or_404(TrainerClientRelationship, trainer_id=request.user.id, client_id=client_id)
        relationship.delete()
        return Response({"message": "Client removed successfully."}, status=status.HTTP_204_NO_CONTENT)

class RemoveTrainerView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, trainer_id):
        # request.user is the client, and trainer_id is from the URL
        relationship = get_object_or_404(TrainerClientRelationship, trainer_id=trainer_id, client_id=request.user.id)
        relationship.delete()
        return Response({"message": "Trainer removed successfully."}, status=status.HTTP_204_NO_CONTENT)


class ProgramViewSet(viewsets.ModelViewSet):
    queryset = Program.objects.all()
    serializer_class = ProgramSerializer

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)

class AddParticipantView(APIView):
    def post(self, request, program_id):
        user_id = request.data.get('user_id')
        try:
            program = Program.objects.get(pk=program_id)
            user = User.objects.get(pk=user_id)
            program.participants.add(user)
            program.save()
            return Response({'status': 'participant added'}, status=status.HTTP_200_OK)
        except Program.DoesNotExist:
            return Response({'error': 'Program does not exist'}, status=status.HTTP_400_BAD_REQUEST)
        except User.DoesNotExist:
            return Response({'error': 'User does not exist'}, status=status.HTTP_400_BAD_REQUEST)
        
class RemoveParticipantView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, program_id):
        user = request.user
        try:
            program = Program.objects.get(pk=program_id)
            if user not in program.participants.all():
                return Response({'error': 'User is not a participant of this program'}, status=status.HTTP_400_BAD_REQUEST)

            program.participants.remove(user)
            program.save()
            return Response({'status': 'participant removed'}, status=status.HTTP_200_OK)
        except Program.DoesNotExist:
            return Response({'error': 'Program does not exist'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
class UserParticipatingProgramsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        programs = Program.objects.filter(participants=user)
        
        serializer = ProgramSerializer(programs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class UserProgramViewSet(viewsets.ModelViewSet):
    serializer_class = ProgramSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Program.objects.filter(creator=self.request.user)

class WorkoutViewSet(viewsets.ModelViewSet):
    queryset = Workout.objects.all()
    serializer_class = WorkoutSerializer

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user)

class UpdateWorkoutOrderAPIView(APIView):
    def post(self, request, *args, **kwargs):
        serializer = WorkoutOrderSerializer(data=request.data, many=True)
        if serializer.is_valid():
            updates = []
            errors = []
            for item in serializer.validated_data:
                try:
                    workout = Workout.objects.get(id=item['id'])
                    workout.order = item['order']
                    workout.save()
                    updates.append({'id': workout.id, 'order': workout.order})
                except ObjectDoesNotExist:
                    errors.append({'id': item['id'], 'error': 'Workout does not exist'})
            
            if errors:
                return Response({'status': 'partial_success', 'updated': updates, 'errors': errors}, status=status.HTTP_206_PARTIAL_CONTENT)
            return Response({'status': 'success', 'updated': updates}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class UpdateExerciseOrderAPIView(APIView):
    def post(self, request, *args, **kwargs):
        serializer = ExerciseOrderSerializer(data=request.data, many=True)
        if serializer.is_valid():
            for item in serializer.validated_data:
                exercise = WorkoutExercise.objects.get(id=item['id'])
                exercise.order = item['order']
                exercise.save()
            return Response({'status': 'success'}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class UserWorkoutViewSet(viewsets.ModelViewSet):
    queryset = Workout.objects.all()
    serializer_class = WorkoutSerializer

    def get_queryset(self):
        return Workout.objects.filter(creator=self.request.user)

class ExerciseViewSet(viewsets.ModelViewSet):
    serializer_class = ExerciseSerializer

    def get_queryset(self):
        return Exercise.objects.filter(creator=None)

class UserExerciseViewSet(viewsets.ModelViewSet):
    serializer_class = ExerciseSerializer

    def get_queryset(self):
        user = self.request.user
        return Exercise.objects.filter(creator=user)

class ExerciseSetCreateAPIView(APIView):
    def post(self, request, log_id):
        # Retrieve the associated ExerciseLog
        exercise_log = ExerciseLog.objects.get(id=log_id)

        # Determine the next set number
        if exercise_log.exercise_sets.count() > 0:
            last_set_number = exercise_log.exercise_sets.last().set_number
        else:
            last_set_number = 0

        new_set_number = last_set_number + 1

        # Create a new ExerciseSet with the next set number
        exercise_set = ExerciseSet(
            exercise_log=exercise_log,
            set_number=new_set_number,
            reps=request.data.get('reps', None),  # Optional data from request
            weight_used=request.data.get('weight_used', None)  # Optional data from request
        )
        exercise_set.save()

        # Optionally update the ExerciseLog sets_completed field
        exercise_log.sets_completed += 1
        exercise_log.save()

        # Serialize and return the new ExerciseSet
        serializer = ExerciseSetSerializer(exercise_set)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
class DeleteLastExerciseSetAPIView(APIView):
    def delete(self, request, log_id):
        try:
            exercise_log = ExerciseLog.objects.get(id=log_id)
        except ExerciseLog.DoesNotExist:
            return Response({'error': 'Exercise log not found'}, status=status.HTTP_404_NOT_FOUND)

        # Retrieve the corresponding WorkoutExercise
        workout_exercise = exercise_log.workout_exercise

        # Get the last ExerciseSet
        last_exercise_set = exercise_log.exercise_sets.order_by('set_number').last()

        if last_exercise_set:
            # Check if the current number of sets in the log exceeds the defined number of sets in the workout
            if exercise_log.exercise_sets.count() > workout_exercise.sets:
                last_exercise_set.delete()
                # Update sets_completed if necessary
                exercise_log.sets_completed = max(0, exercise_log.sets_completed - 1)
                exercise_log.save()
                return Response({'message': 'Last set deleted successfully'}, status=status.HTTP_204_NO_CONTENT)
            else:
                return Response({'error': 'Cannot delete the set. The number of sets does not exceed the workout plan.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({'error': 'No sets to delete'}, status=status.HTTP_404_NOT_FOUND)

class VideoUploadAPI(APIView):
    def patch(self, request, set_id):
        try:
            exercise_set = ExerciseSet.objects.get(id=set_id)
            serializer = ExerciseSetVideoSerializer(exercise_set, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except ExerciseSet.DoesNotExist:
            return Response({'error': 'ExerciseSet not found'}, status=status.HTTP_404_NOT_FOUND)
        
class DeleteVideoAPIView(APIView):
    def delete(self, request, set_id):
        try:
            exercise_set = ExerciseSet.objects.get(id=set_id)
            exercise_set.video.delete()  # This deletes the file from storage and removes the association
            exercise_set.video = None  # Ensure the field is set to None after deletion
            exercise_set.save()
            return Response({'message': 'Video deleted successfully'}, status=status.HTTP_204_NO_CONTENT)
        except ExerciseSet.DoesNotExist:
            return Response({'error': 'ExerciseSet not found'}, status=status.HTTP_404_NOT_FOUND)
        
class ProfilePictureUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        print("Received request data:", request.data)  # Log the request data
        user = request.user
        serializer = UserSerializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        print("Serializer errors:", serializer.errors)  # Log serializer errors
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class WorkoutExerciseViewSet(viewsets.ModelViewSet):
    queryset = WorkoutExercise.objects.all()
    serializer_class = WorkoutExerciseSerializer

class ExerciseLogCreationAPI(views.APIView):
    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            # First, check if the workout session exists
            workout_session_id = request.data.get('workout_session')
            try:
                workout_session = WorkoutSession.objects.get(id=workout_session_id)
            except WorkoutSession.DoesNotExist:
                return Response({"error": "Workout session not found"}, status=status.HTTP_404_NOT_FOUND)

            # Create WorkoutExercise
            workout_exercise_data = {
                'exercise_name': request.data.get('exercise_name'),
                'sets': 0,
                'reps': 0
            }
            workout_exercise_serializer = WorkoutExerciseSerializer(data=workout_exercise_data, context={'request': request})
            if workout_exercise_serializer.is_valid():
                workout_exercise = workout_exercise_serializer.save()
            else:
                return Response(workout_exercise_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
            exercise_log = ExerciseLog.objects.create(
                    workout_session=workout_session,
                    workout_exercise=workout_exercise,
                    sets_completed=0
                )

            exercise_log_serializer = ExerciseLogSerializer(exercise_log)
            if exercise_log_serializer.is_valid:
                return Response({
                    "success": "Exercise log created successfully.",
                    "exercise_log": exercise_log_serializer.data
                }, status=status.HTTP_201_CREATED)
            else:
                return Response({
                    "failed": "Exercise log create failed.",
                    "exercise_log": exercise_log_serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)

class ProgramCreateView(APIView):
    def post(self, request, *args, **kwargs):
        serializer = ProgramSerializer(data=request.data)
        if serializer.is_valid():
            # Manually set the creator to the currently authenticated user
            serializer.save(creator=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
#APIs for Workout Journal

class SetActiveProgramView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        program_id = request.data.get('program_id')
        if not program_id:
            return Response({'error': 'Program ID is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user_program_progress = set_or_update_user_program_progress(request.user, program_id)
            return Response({'message': 'Program set as active successfully.'})
        except Program.DoesNotExist:
            return Response({'error': 'Program not found.'}, status=status.HTTP_404_NOT_FOUND)

class SetInactiveProgramView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        program_id = request.data.get('program_id')
        if not program_id:
            return Response({'error': 'Program ID is required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Attempt to retrieve the program and the user's program progress.
            program = Program.objects.get(id=program_id)
            user_program_progress = UserProgramProgress.objects.get(user=request.user, program=program)
            
            # Set the program to inactive if it's currently active.
            if user_program_progress.is_active:
                user_program_progress.is_active = False
                user_program_progress.save()
                return Response({'message': 'Program set to inactive successfully.'})
            else:
                return Response({'error': 'Program is already inactive.'}, status=status.HTTP_400_BAD_REQUEST)
                
        except Program.DoesNotExist:
            return Response({'error': 'Program not found.'}, status=status.HTTP_404_NOT_FOUND)
        except UserProgramProgress.DoesNotExist:
            return Response({'error': 'User program progress not found.'}, status=status.HTTP_404_NOT_FOUND)
        
class CreateAndActivateProgramView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Step 1: Create the program
        serializer = ProgramSerializer(data=request.data)
        if serializer.is_valid():
            program = serializer.save(creator=request.user)

            # Step 2: Set the program as active
            try:
                user_program_progress = set_or_update_user_program_progress(request.user, program.id)
                return Response({'message': 'Program created and set as active successfully.', 'program': ProgramSerializer(program).data}, status=status.HTTP_201_CREATED)
            except Program.DoesNotExist:  # This should theoretically never happen since we just created the program
                return Response({'error': 'An unexpected error occurred.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
class ActiveProgramView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            user_program_progress = UserProgramProgress.objects.get(user=request.user, is_active=True)
            program = user_program_progress.program
            serializer = ProgramSerializer(program)
            return Response(serializer.data)
        except UserProgramProgress.DoesNotExist:
            return Response({'error': 'No active program found.'}, status=status.HTTP_404_NOT_FOUND)
        
class StartWorkoutSessionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        workout_id = request.data.get('workout_id')
        try:
            # Ensure there is no other active session
            if WorkoutSession.objects.filter(
                user_program_progress__user=request.user,
                user_program_progress__is_active=True,
                active=True
            ).exists():
                return Response({'error': 'Another workout session is already active.'}, status=400)

            workout_session = start_workout_session(request.user, workout_id)
            return Response({'message': 'Workout session started successfully.', 'session_id': workout_session.id})

        except Exception as e:
            return Response({'error': str(e)}, status=400)
        
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_active_session(request):
    current_user = request.user
    active_session = WorkoutSession.objects.filter(
        user_program_progress__user=current_user,
        active=True,
        completed=False
    ).first()

    if active_session:
        # Serialize the active session
        serializer = WorkoutSessionSerializer(active_session)
        return Response(serializer.data)
    else:
        # If no active session is found, return a different response
        return Response({'active': False})
    
class EndWorkoutSession(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        try:
            session = WorkoutSession.objects.get(id=session_id)
            if not session.completed:
                session.completed = True
                session.active = False
                session.save()
                return Response({'status': 'success', 'message': 'Workout session ended successfully.'})
            else:
                return Response({'status': 'error', 'message': 'Session already completed.'}, status=status.HTTP_400_BAD_REQUEST)
        except WorkoutSession.DoesNotExist:
            return Response({'status': 'error', 'message': 'Session not found.'}, status=status.HTTP_404_NOT_FOUND)
        
class UserWorkoutSessionView(viewsets.ModelViewSet):
    serializer_class = WorkoutSessionSerializer
    #permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return WorkoutSession.objects.filter(user_program_progress__user=self.request.user)
        
class WorkoutSessionDetailView(RetrieveAPIView):
    queryset = WorkoutSession.objects.all()
    serializer_class = WorkoutSessionSerializer
    lookup_field = 'id'
        
class ExerciseLogViewSet(RetrieveUpdateAPIView):
    queryset = ExerciseLog.objects.all()
    serializer_class = ExerciseLogSerializer

    def perform_update(self, serializer):
    # Custom update logic here
        serializer.save()

class ExerciseSetViewSet(RetrieveUpdateAPIView):
    queryset = ExerciseSet.objects.all()
    serializer_class = ExerciseSetSerializer

    def perform_update(self, serializer):
    # Custom update logic here
        serializer.save()

class ExerciseSetHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, exercise_id):
        # Filter WorkoutExercises by exercise_id
        workout_exercises = WorkoutExercise.objects.filter(exercise__id=exercise_id)

        # Retrieve all the ExerciseLog ids associated with these WorkoutExercises
        workout_exercises_ids = [we.id for we in workout_exercises]

        # Fetch ExerciseSets linked through these WorkoutExercises ids and filter by weight_used not null or zero
        exercise_sets = ExerciseSet.objects.filter(
            exercise_log__workout_exercise__id__in=workout_exercises_ids,
            exercise_log__workout_session__user_program_progress__user=request.user,
            weight_used__gt=0  # Filters out ExerciseSets where weight_used is greater than 0
        )
        serializer = ExerciseSetSerializer(exercise_sets, many=True)
        return Response(serializer.data)


#openai api


class OpenAIView(APIView):
    def post(self, request, *args, **kwargs):
        user_prompt = request.data.get('prompt')
        program_id = request.data.get('program_id')

        if not user_prompt or not program_id:
            return Response({"error": "Missing prompt or phase"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            now = timezone.now()

            # Calculate the start of the current week (most recent Sunday at midnight)
            start_of_week = now - timedelta(days=(now.weekday() + 1) % 7)
            start_of_week = timezone.make_aware(datetime.combine(start_of_week.date(), time.min))

            # Calculate the end of the week (end of Saturday)
            end_of_week = start_of_week + timedelta(days=6)
            end_of_week = timezone.make_aware(datetime.combine(end_of_week.date(), time.max))

            # Check the count of AI-generated workouts for the current week
            ai_workout_count = Workout.objects.filter(
                creator=request.user,
                is_ai_generated=True,
                created_at__range=(start_of_week, end_of_week)
            ).count()

            if ai_workout_count >= 3:
                return Response({"error": "You have reached the limit of 3 AI-generated workouts per week."}, status=status.HTTP_400_BAD_REQUEST)
            
            openai.api_key = settings.API_KEY
            response = openai.chat.completions.create(
                model="gpt-4o",
                response_format={"type":"json_object"},
                messages=[
        {
            "role": "system",
            "content": "You are Professional NSCA Certified Strength and Conditioning Specialist. Write a workout based on the user's prompts following all NSCA guidelines. If the users prompt contains text that is unrelated, send them back the infamous One Punch Man workout formatted in the data structure that follows(100 situps, 100 pushups, 100 squats, and a 10-km run). Your response should be a valid JSON object structured as follows: "
                       "{"
                       "\"workout_exercises\": ["
                       "    {"
                       "        \"exercise_name\": \"<Name of the exercise(max_length=45)>\","
                       "        \"sets\": <int>,"
                       "        \"reps\": <int>,"
                       "        \"note\": \"<Any specific note for the exercise>\""
                       "    },"
                       "    {...additional exercises}"
                       "],"
                       "\"name\": \"<Name of the workout program(max_length=45)>\""
                       "}. Use double quotes for keys and string values. Replace placeholder text with actual exercise details."
        },
        {"role": "user", "content": user_prompt}
    ]
            )
            workout_data = json.loads(response.choices[0].message.content)
            workout_data['program'] = program_id

            serializer = WorkoutSerializer(data=workout_data, context={'request': request})
            if serializer.is_valid():
                serializer.save(creator=request.user, is_ai_generated=True)  # Assuming your Workout model has a creator field
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

class OpenAIProgramView(APIView):
    def post(self, request, *args, **kwargs):
        user_prompt = request.data.get('prompt')

        if not user_prompt:
            return Response({"error": "Missing prompt"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            now = timezone.now()

            # Calculate the start of the current week (most recent Sunday at midnight)
            start_of_week = now - timedelta(days=(now.weekday() + 1) % 7)
            start_of_week = timezone.make_aware(datetime.combine(start_of_week.date(), time.min))

            # Calculate the end of the current week (end of Saturday)
            end_of_week = start_of_week + timedelta(days=6)
            end_of_week = timezone.make_aware(datetime.combine(end_of_week.date(), time.max))

            # Check the count of AI-generated programs for the current week
            ai_program_count = Program.objects.filter(
                creator=request.user,
                is_ai_generated=True,
                created_at__range=(start_of_week, end_of_week)
            ).count()

            if ai_program_count >= 3:
                return Response({"error": "You have reached the limit of 3 AI programs per week."}, status=status.HTTP_400_BAD_REQUEST)
            
            openai.api_key = settings.API_KEY
            response = openai.chat.completions.create(
                model="gpt-4o",
                response_format={"type":"json_object"},
                messages=[
        {
        "role": "system",
        "content": "You are a Professional NSCA Certified Strength and Conditioning Specialist. Write a workout program based on the user's prompts following all NSCA guidelines. Your response should be a valid JSON object structured as follows:" 
        "{"
            "\"name\": \"<Name of the workout program(max_length=45)>\","
            "\"description\": \"<Description of the workout program>\","
            "\"workouts\": ["
                "{"
                    "\"name\": \"<Name of the workout(max_length=45)>\","
                    "\"workout_exercises\": ["
                        "{"
                            "\"exercise_name\": \"<Name of the exercise(max_length=45)>\","
                            "\"sets\": <type:int>,"
                            "\"reps\": <type:int>,"
                            "\"note\": \"<Specific note for the exercise>\""
                        "},"
                        "{"
                            "\"exercise_name\": \"<Name of another exercise>\","
                            "\"sets\": <type:int>,"
                            "\"reps\": <type:int>,"
                            "\"note\": \"<Specific note for another exercise>\""
                        "}"
                        "    {...additional exercises}"
                    "]"
                "}"
                "{...additional workouts}"
            "]"
        "}.Replace placeholder text with actual program and exercise details."
        },
        {"role": "user", "content": user_prompt}
    ]
            )
            program_data = json.loads(response.choices[0].message.content)


            serializer = ProgramSerializer(data=program_data, context={'request': request})
            if serializer.is_valid():
                program = serializer.save(creator=request.user, is_ai_generated=True)  # Assuming your program model has a creator field
                set_or_update_user_program_progress(request.user, program.id)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
class AIProgramLimitView(APIView):
    def get(self, request):
        now = timezone.now()
        start_of_week = now - timedelta(days=(now.weekday() + 1) % 7)
        start_of_week = timezone.make_aware(datetime.combine(start_of_week.date(), datetime.min.time()))
        end_of_week = start_of_week + timedelta(days=6)
        end_of_week = timezone.make_aware(datetime.combine(end_of_week.date(), datetime.max.time()))

        ai_program_count = Program.objects.filter(
            creator=request.user,
            is_ai_generated=True,
            created_at__range=[start_of_week, end_of_week]
        ).count()

        remaining = 3 - ai_program_count  # Assuming the limit is 5 programs per week
        return Response({"remaining": remaining}, status=status.HTTP_200_OK)
    
class AIWorkoutLimitView(APIView):
    def get(self, request):
        now = timezone.now()
        start_of_week = now - timedelta(days=(now.weekday() + 1) % 7)
        start_of_week = timezone.make_aware(datetime.combine(start_of_week.date(), datetime.min.time()))
        end_of_week = start_of_week + timedelta(days=6)
        end_of_week = timezone.make_aware(datetime.combine(end_of_week.date(), datetime.max.time()))

        ai_workout_count = Workout.objects.filter(
            creator=request.user,
            is_ai_generated=True,
            created_at__range=[start_of_week, end_of_week]
        ).count()

        remaining = 3 - ai_workout_count  # Assuming the limit is 5 workouts per week
        return Response({"remaining": remaining}, status=status.HTTP_200_OK)
        
#APIs for Messages
        
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

class MessageViewSet(viewsets.ModelViewSet):
    queryset = Message.objects.all()
    serializer_class = MessageSerializer

class ChatSessionViewSet(viewsets.ModelViewSet):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer

    def destroy(self, request, *args, **pk):
        chat_session = self.get_object()
        messages = Message.objects.filter(chat_session=chat_session)
        messages.delete()  # Delete all messages associated with the chat session
        chat_session.delete()  # Delete the chat session
        return Response(status=status.HTTP_204_NO_CONTENT)

class ChatSessionMessageViewSet(viewsets.ViewSet):
    def retrieve_or_create_session_get_messages(self, request, other_user_id=None):
        chat_session = get_chat_session(request.user.id, other_user_id)
        if chat_session:
            messages = get_messages_for_session(chat_session)
            serializer = MessageSerializer(messages, many=True)
            return Response(serializer.data)
        return Response({"message": "No chat session found"}, status=404)
    
class UserChatSessionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        chat_sessions = ChatSession.objects.filter(participants=user).distinct()
        serializer = ChatSessionSerializer(chat_sessions, many=True, context={'request': request})
        return Response(serializer.data)
    
#dataCharts
    
class WorkoutSessionsLast3MonthsView(APIView):
    def get(self, request):
        end_date = now()
        start_date = end_date - timedelta(days=90)  # Approximately 3 months

        sessions = WorkoutSession.objects.filter(
            user_program_progress__user=request.user,
            date__range=(start_date, end_date)
        ).order_by('date')

        chart_data = self.process_sessions_by_week(sessions, start_date, end_date)
        return Response(chart_data)

    def process_sessions_by_week(self, sessions, start_date, end_date):
        data = OrderedDict()
        current_date = start_date
        while current_date <= end_date:
            week_str = current_date.strftime('%Y-%U')  # Maintain unique year-week identification
            data[week_str] = 0
            current_date += timedelta(days=7)
        
        end_week_str = end_date.strftime('%Y-%U')
        if end_week_str not in data:
            data[end_week_str] = 0

        for session in sessions:
            week_str = session.date.strftime('%Y-%U')
            if week_str in data:
                data[week_str] += 1

        # Convert to a format suitable for Recharts, consider transforming week_str for frontend display if needed
        chart_data = [{'week': week, 'workouts': count} for week, count in data.items()]
        return chart_data
    
class Exercise1RMView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, exercise_id):
        # Calculate the date 6 months ago from today
        six_months_ago = now() - timedelta(days=180)
        
        # Fetch exercise sets for the given exercise in the last 6 months for the current user
        exercise_sets = ExerciseSet.objects.filter(
            exercise_log__workout_exercise__exercise_id=exercise_id,
            exercise_log__workout_session__date__gte=six_months_ago,
            exercise_log__workout_session__user_program_progress__user=request.user
        ).select_related('exercise_log__workout_exercise__exercise').exclude(weight_used__isnull=True).exclude(reps__isnull=True).order_by('exercise_log__workout_session__date')
        
        # Prepare the data for the chart
        chart_data = self.prepare_chart_data(exercise_sets)
        
        return Response(chart_data)

    def prepare_chart_data(self, exercise_sets):
        chart_data = {}
        for exercise_set in exercise_sets:
            # Calculate 1RM using the Epley formula for each set
            one_rm = exercise_set.weight_used * (1 + exercise_set.reps / 30.0)
            one_rm = round(one_rm, 1)
            day = exercise_set.exercise_log.workout_session.date.strftime('%Y-%m-%d')
            
            # If multiple sets are done on the same day, store the max 1RM
            if day not in chart_data or one_rm > chart_data[day]:
                chart_data[day] = one_rm
        
        # Convert the chart data dictionary to a list of objects
        return [{'day': day, 'one_rm': one_rm} for day, one_rm in chart_data.items()]
    
class ExercisesWithWeightsView(APIView):
    permission_classes = [IsAuthenticated]  # Ensure the user is authenticated

    def get(self, request):
        user = request.user
        
        # Adjusted subquery to check for ExerciseSets linked to the user's WorkoutExercises
        has_weighted_sets = ExerciseSet.objects.filter(
            exercise_log__workout_session__user_program_progress__user=user,
            exercise_log__workout_exercise__exercise_id=OuterRef('pk'),  # Note the change here
            weight_used__isnull=False
        )

        # Query for Exercises with at least one ExerciseSet with weight_used for the user
        exercises = Exercise.objects.annotate(
            has_weighted_set=Exists(has_weighted_sets)
        ).filter(has_weighted_set=True).distinct()
        
        # Prepare and return the response with Exercise details
        exercises_data = [{'id': exercise.id, 'name': exercise.name} for exercise in exercises]
        return Response(exercises_data)
    
class CumulativeWeightView(APIView):
    def get(self, request):
        user = request.user  # Assuming you have user authentication set up
        end_date = now().date()
        start_date = end_date - timedelta(days=6)  # Last 7 days including today

        # Fetch all sessions for the user in the last 7 days
        sessions = WorkoutSession.objects.filter(
            user_program_progress__user=user,
            date__date__range=(start_date, end_date)
        ).prefetch_related('exercise_logs__exercise_sets')

        # Prepare data structure for cumulative weights
        cumulative_weights = {date.strftime('%Y-%m-%d'): 0 for date in [start_date + timedelta(days=x) for x in range((end_date-start_date).days + 1)]}

        # Calculate cumulative weight for each day
        for session in sessions:
            session_date = session.date.date().strftime('%Y-%m-%d')
            for log in session.exercise_logs.all():
                for exercise_set in log.exercise_sets.all():
                    if exercise_set.weight_used and exercise_set.reps:
                        cumulative_weights[session_date] += (exercise_set.weight_used * exercise_set.reps)
        
        # Format the data for the response
        response_data = [{'date': date, 'total_weight_lifted': weight} for date, weight in cumulative_weights.items()]

        return Response(response_data)
    
#client progress
    
class ClientWorkoutSessionView(viewsets.ModelViewSet):
    serializer_class = WorkoutSessionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        client_id = self.kwargs.get('client_id')
        client = get_object_or_404(User, pk=client_id)

        # Ensure the client is one of the user's clients
        if not self.request.user.clients.filter(pk=client_id).exists():
            return WorkoutSession.objects.none()  # Return an empty queryset if unauthorized

        return WorkoutSession.objects.filter(user_program_progress__user=client)

class ClientWorkoutSessionsLast3MonthsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, client_id):
        client = get_object_or_404(User, pk=client_id)

        # Ensure the client is one of the user's clients
        if not request.user.clients.filter(pk=client_id).exists():
            return Response({"detail": "Client not found or not authorized."}, status=403)

        end_date = now()
        start_date = end_date - timedelta(days=90)  # Approximately 3 months

        sessions = WorkoutSession.objects.filter(
            user_program_progress__user=client,
            date__range=(start_date, end_date)
        ).order_by('date')

        chart_data = self.process_sessions_by_week(sessions, start_date, end_date)
        return Response(chart_data)

    def process_sessions_by_week(self, sessions, start_date, end_date):
        data = OrderedDict()
        current_date = start_date
        while current_date <= end_date:
            week_str = current_date.strftime('%Y-%U')  # Maintain unique year-week identification
            data[week_str] = 0
            current_date += timedelta(days=7)
        
        end_week_str = end_date.strftime('%Y-%U')
        if end_week_str not in data:
            data[end_week_str] = 0

        for session in sessions:
            week_str = session.date.strftime('%Y-%U')
            if week_str in data:
                data[week_str] += 1

        # Convert to a format suitable for Recharts, consider transforming week_str for frontend display if needed
        chart_data = [{'week': week, 'workouts': count} for week, count in data.items()]
        return chart_data

class ClientExercise1RMView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, client_id, exercise_id):
        client = get_object_or_404(User, pk=client_id)

        # Ensure the client is one of the user's clients
        if not request.user.clients.filter(pk=client_id).exists():
            return Response({"detail": "Client not found or not authorized."}, status=403)

        # Calculate the date 6 months ago from today
        six_months_ago = now() - timedelta(days=180)
        
        # Fetch exercise sets for the given exercise in the last 6 months
        exercise_sets = ExerciseSet.objects.filter(
            exercise_log__workout_exercise__exercise_id=exercise_id,
            exercise_log__workout_session__user_program_progress__user=client,
            exercise_log__workout_session__date__gte=six_months_ago
        ).select_related('exercise_log__workout_exercise__exercise').exclude(weight_used__isnull=True).exclude(reps__isnull=True).order_by('exercise_log__workout_session__date')
        
        # Prepare the data for the chart
        chart_data = self.prepare_chart_data(exercise_sets)
        
        return Response(chart_data)

    def prepare_chart_data(self, exercise_sets):
        chart_data = {}
        for exercise_set in exercise_sets:
            # Calculate 1RM using the Epley formula for each set
            one_rm = exercise_set.weight_used * (1 + exercise_set.reps / 30.0)
            one_rm = round(one_rm, 1)
            day = exercise_set.exercise_log.workout_session.date.strftime('%Y-%m-%d')
            
            # If multiple sets are done on the same day, store the max 1RM
            if day not in chart_data or one_rm > chart_data[day]:
                chart_data[day] = one_rm
        
        # Convert the chart data dictionary to a list of objects
        return [{'day': day, 'one_rm': one_rm} for day, one_rm in chart_data.items()]

class ClientExercisesWithWeightsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, client_id):
        client = get_object_or_404(User, pk=client_id)

        # Ensure the client is one of the user's clients
        if not request.user.clients.filter(pk=client_id).exists():
            return Response({"detail": "Client not found or not authorized."}, status=403)

        # Adjusted subquery to check for ExerciseSets linked to the client's WorkoutExercises
        has_weighted_sets = ExerciseSet.objects.filter(
            exercise_log__workout_session__user_program_progress__user=client,
            exercise_log__workout_exercise__exercise_id=OuterRef('pk'),  # Note the change here
            weight_used__isnull=False
        )

        # Query for Exercises with at least one ExerciseSet with weight_used for the client
        exercises = Exercise.objects.annotate(
            has_weighted_set=Exists(has_weighted_sets)
        ).filter(has_weighted_set=True).distinct()
        
        # Prepare and return the response with Exercise details
        exercises_data = [{'id': exercise.id, 'name': exercise.name} for exercise in exercises]
        return Response(exercises_data)

class ClientCumulativeWeightView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, client_id):
        client = get_object_or_404(User, pk=client_id)

        # Ensure the client is one of the user's clients
        if not request.user.clients.filter(pk=client_id).exists():
            return Response({"detail": "Client not found or not authorized."}, status=403)

        end_date = now().date()
        start_date = end_date - timedelta(days=6)  # Last 7 days including today

        # Fetch all sessions for the client in the last 7 days
        sessions = WorkoutSession.objects.filter(
            user_program_progress__user=client,
            date__date__range=(start_date, end_date)
        ).prefetch_related('exercise_logs__exercise_sets')

        # Prepare data structure for cumulative weights
        cumulative_weights = {date.strftime('%Y-%m-%d'): 0 for date in [start_date + timedelta(days=x) for x in range((end_date-start_date).days + 1)]}

        # Calculate cumulative weight for each day
        for session in sessions:
            session_date = session.date.date().strftime('%Y-%m-%d')
            for log in session.exercise_logs.all():
                for exercise_set in log.exercise_sets.all():
                    if exercise_set.weight_used and exercise_set.reps:
                        cumulative_weights[session_date] += (exercise_set.weight_used * exercise_set.reps)
        
        # Format the data for the response
        response_data = [{'date': date, 'total_weight_lifted': weight} for date, weight in cumulative_weights.items()]

        return Response(response_data)
